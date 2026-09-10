""" GUI for the arbitrary waveform generator category.

Laid out like the oscilloscope panel: a plot on the left, per-channel controls on the right, every
section a CollapsiblePanel inside splitters. Two things are specific to this category:

- **The plot is an estimate, not a measurement.** A generator reports its settings, not its
  output, so the trace is *computed* from the tracked waveform type, frequency, amplitude, offset,
  phase, duty cycle and polarity. One subdued line under the plot says so (full caveats in its
  tooltip), and the figure carries a small mark too, so a screenshot of the plot alone still says
  what it is. `estimate_waveform()` is a plain function with no Qt in it, so what it draws can be
  tested directly.
- **Channels can sit side by side or in tabs**, chosen from the window's View menu rather than a
  control on the panel. The per-channel controls are built once and moved between the two
  containers rather than rebuilt, so switching layout never drops a setpoint or a pending request.
"""

import numpy as np

from constellation.base import *
from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import (QWidget, QGridLayout, QVBoxLayout, QLabel, QStackedWidget, QTabWidget)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDoubleValidator, QAction, QActionGroup

AWG = ArbitraryWaveformGenerator

WAVE_LABELS = {
	AWG.WAVE_SINE: "Sine", AWG.WAVE_SQUARE: "Square", AWG.WAVE_RAMP: "Ramp",
	AWG.WAVE_PULSE: "Pulse", AWG.WAVE_NOISE: "Noise", AWG.WAVE_ARB: "Arbitrary", AWG.WAVE_DC: "DC",
}

POLARITY_LABELS = {AWG.POLARITY_NORMAL: "Normal", AWG.POLARITY_INVERTED: "Inverted"}

# Output load is ohms or the high-Z sentinel. Offered as a choice of the two settings nearly
# everyone uses; an instrument set to something else (75 ohm, say) still shows it on the PV row,
# because the choice display only ever moves to a value it knows and never invents one.
LOAD_CHOICES = [AWG.LOAD_HIGH_Z, 50.0]
LOAD_LABELS = {AWG.LOAD_HIGH_Z: "High-Z", 50.0: "50 Ω"}

LAYOUT_SIDE_BY_SIDE = "side-by-side"
LAYOUT_TABS = "tabs"
LAYOUT_LABELS = {LAYOUT_SIDE_BY_SIDE: "Channels Side by Side", LAYOUT_TABS: "Channels in Tabs"}

# One short line on the panel; the caveats live in its tooltip. The controls, not the warning, are
# what should draw the eye.
ESTIMATE_DISCLAIMER = "Estimated from settings, not measured."

ESTIMATE_DISCLAIMER_DETAIL = (
	"This plot is computed from the channel settings Constellation is tracking. It is not measured "
	"from the output and is not guaranteed to match it: it assumes ideal edges and the load the "
	"instrument is set to assume, cannot know ramp symmetry or the contents of an arbitrary "
	"waveform, and is only as current as the last state poll.")

# Parameters the estimate is drawn from. Only a change to one of these redraws the plot - the
# bridge polls every couple of seconds, and redrawing an unchanged figure each time is wasted work.
_ESTIMATE_FIELDS = ("waveform_type", "frequency", "amplitude", "offset", "phase", "duty_cycle",
	"output_enable", "output_polarity")

# Seeded, so the noise estimate does not crawl on every redraw and read as a live signal.
_NOISE_SEED = 12345

def estimate_waveform(chan, t):
	''' The output a channel's settings imply, sampled at times `t`.

	Args:
		chan: Anything with the AWGChannelState attributes - a channel state, or a stand-in.
		t (array): Sample times, seconds.

	Returns:
		tuple: (y, note). `y` is an array of volts, or None when the settings cannot be turned into
			a waveform at all; `note` is a short human-readable caveat, or None.

	The model is deliberately simple, and each simplification is something the real output can
	differ by: edges are instantaneous, a ramp is a rising sawtooth (symmetry is not tracked, and
	the two drivers' instruments default it differently), noise is a seeded sample scaled so its
	peak-to-peak roughly matches the amplitude, and amplitude is into the load the instrument is
	set to assume rather than whatever is really connected.
	'''

	t = np.asarray(t, dtype=float)
	wave = chan.waveform_type
	offset = chan.offset

	if wave is None:
		return None, "waveform type not known yet"
	if wave == AWG.WAVE_ARB:
		return None, "arbitrary waveform - its contents are not tracked, so it cannot be estimated"

	if offset is None:
		return None, "offset not known yet"

	if wave == AWG.WAVE_DC:
		return np.full_like(t, float(offset)), None

	amplitude = chan.amplitude
	if amplitude is None:
		return None, "amplitude not known yet"

	half = float(amplitude) / 2
	note = None

	if wave == AWG.WAVE_NOISE:
		# Gaussian noise has no true peak-to-peak; about 6 sigma spans the bulk of it.
		shape = np.random.default_rng(_NOISE_SEED).normal(0.0, 1.0 / 3, t.shape)
		note = "noise is an illustrative random sample, not the instrument's output"

	else:
		freq = chan.frequency
		if freq is None or float(freq) <= 0:
			return None, "frequency not known yet"

		phase = float(chan.phase or 0.0)

		# Fraction of the way through the current cycle, phase included.
		cycle = np.mod(float(freq) * t + phase / 360.0, 1.0)

		if wave == AWG.WAVE_SINE:
			shape = np.sin(2 * np.pi * cycle)

		elif wave in (AWG.WAVE_SQUARE, AWG.WAVE_PULSE):
			duty = chan.duty_cycle
			if duty is None:
				duty = 50.0
				note = "duty cycle not known - drawn at 50%"
			shape = np.where(cycle < float(duty) / 100.0, 1.0, -1.0)

		elif wave == AWG.WAVE_RAMP:
			shape = 2 * cycle - 1
			note = "ramp symmetry is not tracked - drawn as a rising sawtooth"

		else:
			return None, f"no estimate for waveform >{wave}<"

	# Inverting flips the waveform about its offset, not about zero - on both supported
	# instruments, and it is what the hardware suite's polarity prompt checks for.
	if chan.output_polarity == AWG.POLARITY_INVERTED:
		shape = -shape

	return float(offset) + half * shape, note

def estimate_time_axis(channels, periods:float=3.0, min_points:int=2000, max_points:int=50000):
	''' Sample times covering `periods` cycles of the slowest periodic channel shown.

	The slowest, so every channel shows at least that many cycles; the point count rises with the
	fastest one so its edges don't alias into nonsense, up to a cap. With nothing periodic (all DC
	or noise), a fixed 1 ms window.
	'''

	freqs = [float(c.frequency) for c in channels
		if c.waveform_type not in (None, AWG.WAVE_DC, AWG.WAVE_NOISE, AWG.WAVE_ARB)
		and c.frequency is not None and float(c.frequency) > 0]

	if not freqs:
		return np.linspace(0.0, 1e-3, min_points)

	span = periods / min(freqs)
	cycles_of_fastest = span * max(freqs)
	points = int(np.clip(cycles_of_fastest * 200, min_points, max_points))

	return np.linspace(0.0, span, points)

def _time_scale(span:float) -> tuple:
	''' (factor, label) that puts a time span in a readable unit for the x axis. '''

	for factor, label in ((1.0, "s"), (1e-3, "ms"), (1e-6, "µs"), (1e-9, "ns")):
		if span >= factor:
			return factor, label

	return 1e-9, "ns"

@register_gui(ArbitraryWaveformGenerator)
class ArbitraryWaveformGeneratorWidget(InstrumentWidget):
	''' Estimated-output plot on the left, per-channel controls on the right.

	Every control is a setting with a setpoint, so every one is a `Parameter*` - there are no
	actions and no measurements on this category. Output enable is placed first in each channel
	and applied last by the driver's apply_state(); the two orders are deliberately opposite.
	'''

	# Layout the channels start in. Changeable live from the selector in the Channels panel.
	DEFAULT_CHANNEL_LAYOUT = LAYOUT_SIDE_BY_SIDE

	def __init__(self, main_window, bridge:InstrumentBridge, log:plf.LogPile):
		super().__init__(main_window, bridge, log)

		self._channels_built = False
		self._last_estimate_key = None
		self.channel_controls = {}   # channel_num -> dict of Parameter* controls
		self._channel_bodies = {}    # channel_num -> QWidget holding that channel's controls
		self._channel_panels = {}    # channel_num -> CollapsiblePanel used in side-by-side mode
		self._duty_enabled = {}      # channel_num -> the duty control's own enabled state at build
		self._layout_actions = {}    # layout mode -> its View-menu action, while the menu exists
		self.channel_layout = self.DEFAULT_CHANNEL_LAYOUT

		# --- Estimated waveform ---
		self.disclaimer = QLabel(ESTIMATE_DISCLAIMER)
		self.disclaimer.setToolTip(ESTIMATE_DISCLAIMER_DETAIL)
		self.disclaimer.setStyleSheet("QLabel { color: gray; font-size: 11px; }")

		# Per-channel caveats ("ramp symmetry is not tracked", ...), shown only when there are any.
		self.estimate_notes = QLabel("")
		self.estimate_notes.setWordWrap(True)
		self.estimate_notes.setStyleSheet("QLabel { color: gray; font-size: 11px; }")
		self.estimate_notes.setVisible(False)

		self.plot_widget = PlotWidget(main_window, log)

		# Re-lay-out on every draw, resizes included. A one-off tight_layout() at redraw time is
		# computed for whatever size the canvas had then - the first draw happens before the
		# window reaches its final size, which clipped the y-axis label off entirely.
		self.plot_widget.fig1.set_layout_engine("tight")

		wave_layout = QVBoxLayout()
		wave_layout.setContentsMargins(0, 0, 0, 0)
		wave_layout.addWidget(self.plot_widget, 1)
		wave_layout.addWidget(self.disclaimer)
		wave_layout.addWidget(self.estimate_notes)

		self.waveform_box = CollapsiblePanel("Estimated Output")
		self.waveform_box.set_content_layout(wave_layout)

		# --- Channels: one set of controls per channel, housed in either a splitter or a tab
		# widget. Both containers exist from the start and a stacked widget shows one of them;
		# which one is chosen from the View menu (see add_view_actions). ---
		self.channels_splitter = make_splitter(Qt.Orientation.Horizontal)
		self.channels_tabs = QTabWidget()

		self.channels_stack = QStackedWidget()
		self.channels_stack.addWidget(self.channels_splitter)
		self.channels_stack.addWidget(self.channels_tabs)

		channels_layout = QVBoxLayout()
		channels_layout.setContentsMargins(0, 0, 0, 0)
		channels_layout.addWidget(self.channels_stack, 1)

		self.channels_box = CollapsiblePanel("Channels")
		self.channels_box.set_content_layout(channels_layout)

		# --- Assembly: plot to the left of the channels, as asked. ---
		self.body_splitter = make_splitter(Qt.Orientation.Horizontal, self.waveform_box,
			self.channels_box, stretch=[3, 2])

		self.main_layout = QVBoxLayout()
		self.main_layout.addWidget(self.body_splitter, 1)
		self.setLayout(self.main_layout)

	# --- state -----------------------------------------------------------------------------

	def on_state_changed(self, state):

		if not self._channels_built:
			self._build_channels(state)

		self._update_duty_applicability(state)
		self._update_estimate(state)

	# --- channel construction --------------------------------------------------------------

	def _build_channels(self, state):
		''' Built from `state`, never a driver attribute, so an ObserverBridge watching another
		process's generator gets the same panel. '''

		for ch in range(state.first_channel, state.first_channel + state.num_channels):

			controls = self._make_channel_controls(ch)

			body = QWidget()
			grid = QGridLayout()
			grid.setContentsMargins(0, 0, 0, 0)

			for row, control in enumerate(controls.values()):
				grid.addWidget(control, row, 0)

			# Slack lands between whole controls, not inside them.
			grid.setColumnStretch(1, 1)
			grid.setRowStretch(len(controls), 1)
			body.setLayout(grid)

			# The panel is kept for the life of the widget even while its body is in a tab, so
			# switching back is a reparent rather than a rebuild. Folds sideways: the channels sit
			# in a row, and a folded one should hand its width to its neighbours.
			panel = CollapsiblePanel(f"Channel {ch}", fold=Qt.Orientation.Horizontal)
			panel_layout = QVBoxLayout()
			panel_layout.setContentsMargins(0, 0, 0, 0)
			panel.set_content_layout(panel_layout)
			self.channels_splitter.addWidget(panel)

			self.channel_controls[ch] = controls
			self._channel_bodies[ch] = body
			self._channel_panels[ch] = panel
			self._duty_enabled[ch] = controls["duty"].isEnabled()

		self._channels_built = True
		self.set_channel_layout(self.channel_layout)

	def _make_channel_controls(self, ch:int) -> dict:
		''' One channel's controls, in front-panel order. Closures bind `ch` as a default - the
		loop-variable trap the authoring guide warns about. '''

		def args(v, ch=ch):
			return (ch, v)

		return {
			"enable": ParameterToggle(
				self.bridge, f"Output {ch}", get=(lambda s, ch=ch: s.channels[ch].output_enable),
				set_method="set_output_enable", set_args=args, get_args=(ch,)),
			"waveform": ParameterChoice(
				self.bridge, "Waveform", get=(lambda s, ch=ch: s.channels[ch].waveform_type),
				set_method="set_waveform", get_method="get_waveform", set_args=args, get_args=(ch,),
				choices=list(WAVE_LABELS), labels=WAVE_LABELS),
			"frequency": ParameterBox(
				self.bridge, "Frequency", get=(lambda s, ch=ch: s.channels[ch].frequency),
				set_method="set_frequency", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="Hz", tolerance=1e-4, prefixes=("M", "k", "")),
			"amplitude": ParameterBox(
				self.bridge, "Amplitude", get=(lambda s, ch=ch: s.channels[ch].amplitude),
				set_method="set_amplitude", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="Vpp", abs_tolerance=1e-3, prefixes=("", "m")),
			"offset": ParameterBox(
				self.bridge, "Offset", get=(lambda s, ch=ch: s.channels[ch].offset),
				set_method="set_offset", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="V", abs_tolerance=1e-3, prefixes=("", "m")),
			"phase": ParameterBox(
				self.bridge, "Phase", get=(lambda s, ch=ch: s.channels[ch].phase),
				set_method="set_phase", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="deg", abs_tolerance=0.1),
			"duty": ParameterBox(
				self.bridge, "Duty cycle", get=(lambda s, ch=ch: s.channels[ch].duty_cycle),
				set_method="set_duty_cycle", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(0.0, 100.0, 3), unit="%", abs_tolerance=0.1),
			"load": ParameterChoice(
				self.bridge, "Load", get=(lambda s, ch=ch: s.channels[ch].output_load),
				set_method="set_output_load", set_args=args, get_args=(ch,),
				choices=LOAD_CHOICES, labels=LOAD_LABELS),
			"polarity": ParameterChoice(
				self.bridge, "Polarity", get=(lambda s, ch=ch: s.channels[ch].output_polarity),
				set_method="set_output_polarity", set_args=args, get_args=(ch,),
				choices=list(POLARITY_LABELS), labels=POLARITY_LABELS),
		}

	# --- side by side vs tabs --------------------------------------------------------------

	def set_channel_layout(self, mode:str):
		''' Moves the already-built channel bodies into the splitter or the tab widget.

		A reparent, not a rebuild: the Parameter* controls keep their setpoints, pending requests
		and bridge connections across the switch, which a rebuild would silently throw away.
		'''

		if mode not in (LAYOUT_SIDE_BY_SIDE, LAYOUT_TABS):
			raise ValueError(f"Unknown channel layout >{mode}<")

		# Empty the tab widget first. removeTab() detaches without deleting, and doing it
		# explicitly avoids relying on QTabWidget noticing that its pages were reparented away.
		while self.channels_tabs.count():
			self.channels_tabs.removeTab(0)

		for ch in sorted(self._channel_bodies):

			body = self._channel_bodies[ch]

			if mode == LAYOUT_TABS:
				self.channels_tabs.addTab(body, f"Channel {ch}")
			else:
				self._channel_panels[ch].content.layout().addWidget(body)

			# A body is hidden while nothing holds it; make sure it comes back in its new home.
			body.show()

		self.channels_stack.setCurrentWidget(
			self.channels_tabs if mode == LAYOUT_TABS else self.channels_splitter)

		self.channel_layout = mode

		# Keep the View menu's tick in step when the layout is changed from code.
		action = self._layout_actions.get(mode)
		if action is not None and not action.isChecked():
			action.setChecked(True)

	# --- View menu ------------------------------------------------------------------------

	def has_view_actions(self) -> bool:
		return True

	def add_view_actions(self, menu) -> None:
		''' Side by side / tabs, as a mutually exclusive pair. Rebuilt with the menu, so the
		actions are created fresh each time and ticked from the current layout. '''

		group = QActionGroup(menu)
		group.setExclusive(True)

		self._layout_actions = {}

		for mode in (LAYOUT_SIDE_BY_SIDE, LAYOUT_TABS):

			action = QAction(LAYOUT_LABELS[mode], menu)
			action.setCheckable(True)
			action.setChecked(mode == self.channel_layout)
			action.triggered.connect(lambda checked, mode=mode: self.set_channel_layout(mode))

			group.addAction(action)
			menu.addAction(action)
			self._layout_actions[mode] = action

	# --- duty cycle applicability ----------------------------------------------------------

	def _update_duty_applicability(self, state):
		''' Greys the duty-cycle control while the channel is on a waveform that has none.

		Setting a duty cycle on a sine is an instrument error, not a no-op, so the control should
		not invite it. Only ever narrows what the control allowed at build time: a driver that
		cannot do duty cycle at all already has the control disabled, and this must not undo that.
		'''

		for ch, controls in self.channel_controls.items():

			wave = state.channels[ch].waveform_type
			applies = wave in AWG.DUTY_CYCLE_WAVEFORMS

			duty = controls["duty"]
			duty.setEnabled(self._duty_enabled[ch] and applies)
			duty.setToolTip("" if applies else
				f"Duty cycle only applies to {', '.join(WAVE_LABELS[w] for w in AWG.DUTY_CYCLE_WAVEFORMS)} waveforms.")

	# --- estimated output ------------------------------------------------------------------

	def _update_estimate(self, state):

		channels = {ch: state.channels[ch] for ch in self.channel_controls}

		key = tuple((ch, tuple(getattr(c, f) for f in _ESTIMATE_FIELDS)) for ch, c in channels.items())
		if key == self._last_estimate_key:
			return
		self._last_estimate_key = key

		self._redraw_estimate(channels)

	def _redraw_estimate(self, channels:dict):

		ax = self.plot_widget.ax1a
		ax.cla()

		t = estimate_time_axis(channels.values())
		scale, unit = _time_scale(t[-1] if len(t) else 1.0)

		notes = []
		drawn = 0

		for index, (ch, chan) in enumerate(sorted(channels.items())):

			y, note = estimate_waveform(chan, t)

			if note:
				notes.append(f"Ch{ch}: {note}")
			if y is None:
				continue

			# A disabled output is 0 V at the connector, but a flat line at zero is useless while
			# setting a channel up. Draw what it is configured to produce, dashed and labelled, so
			# the difference is visible rather than implied.
			enabled = bool(chan.output_enable)
			label = f"Ch{ch}" if enabled else f"Ch{ch} (output off)"

			ax.plot(t / scale, y, color=f"C{index}", label=label,
				linestyle="-" if enabled else "--", alpha=1.0 if enabled else 0.5)
			drawn += 1

		ax.grid(True)
		ax.set_xlabel(f"Time [{unit}]")
		ax.set_ylabel("Voltage [V]")
		if drawn:
			ax.legend(loc="upper right")

		# On the figure as well as above it, so a screenshot or saved image of the plot alone
		# still says what it is.
		ax.text(0.01, 0.98, "estimate", transform=ax.transAxes, va="top", ha="left",
			fontsize=7, color="gray", alpha=0.7)

		self.estimate_notes.setText("\n".join(notes))
		self.estimate_notes.setVisible(bool(notes))

		self.plot_widget.fig1.canvas.draw_idle()
