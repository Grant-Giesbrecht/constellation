""" Reference GUI for the oscilloscope category.

Read this file alongside docs/gui_architecture_proposal.md and docs/gui_authoring_guide.md if
you're building a GUI for a different category - the pattern here (Tracked* controls wired to a
bridge, lazy per-channel construction from the first state update, front-panel-style grouping) is
meant to be copied, not reinvented per category.
"""

from constellation.base import *
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import QWidget, QGridLayout, QHBoxLayout, QLabel, QPushButton, QGroupBox
from PyQt6.QtGui import QDoubleValidator

@register_gui(Oscilloscope)
class OscilloscopeWidget(InstrumentWidget):
	''' Groups controls the way a real scope's front panel does: one box per channel, a Trigger
	box, a Horizontal/timebase box, and acquisition controls (Run/Stop/Single/Capture) set apart
	along the top - not a replica of any specific model, just the same logical separation.

	Waveform capture is deliberately NOT part of the automatic state refresh (a full-memory
	capture can legitimately take several seconds - see the RigolDS1000Z waveform-capture work) -
	it only happens when "Capture Waveforms" is clicked, same as it would on real hardware.
	'''

	def __init__(self, main_window, bridge:InstrumentBridge, log:plf.LogPile):
		super().__init__(main_window, bridge, log)

		self._channels_built = False
		self.channel_controls = {}  # channel_num -> dict of Tracked* controls, for anyone who wants to reach in
		self._waveform_cache = {}   # channel_num -> last captured waveform dict

		bridge.command_result.connect(self._on_command_result)

		# --- Acquisition controls: deliberately plain buttons, not Tracked* - these are actions
		# (run/stop/single/capture), not settings with a confirmable setpoint. ---
		self.run_button = QPushButton("Run")
		self.run_button.clicked.connect(lambda: bridge.request("run_acquisition"))
		self.stop_button = QPushButton("Stop")
		self.stop_button.clicked.connect(lambda: bridge.request("stop_acquisition"))
		self.single_button = QPushButton("Single")
		self.single_button.clicked.connect(lambda: bridge.request("do_single_trigger"))
		self.capture_button = QPushButton("Capture Waveforms")
		self.capture_button.clicked.connect(self._capture_waveforms)

		self.acq_box = QGroupBox("Acquisition")
		acq_layout = QHBoxLayout()
		for b in (self.run_button, self.stop_button, self.single_button, self.capture_button):
			acq_layout.addWidget(b)
		self.acq_box.setLayout(acq_layout)

		# --- Trigger group ---
		self.trigger_box = QGroupBox("Trigger")
		trig_layout = QGridLayout()
		self.trigger_mode = TrackedChoice(
			bridge, "Mode", get=lambda s: s.trigger_mode, set_method="set_trigger_mode",
			choices=[Oscilloscope.TRIG_AUTO, Oscilloscope.TRIG_NORM, Oscilloscope.TRIG_SINGLE])
		self.trigger_level = TrackedValue(
			bridge, "Level", get=lambda s: s.trigger_level, set_method="set_trigger_level",
			validator=QDoubleValidator(), unit="V")
		trig_layout.addWidget(QLabel("Mode:"), 0, 0)
		trig_layout.addWidget(self.trigger_mode, 0, 1)
		trig_layout.addWidget(QLabel("Level:"), 1, 0)
		trig_layout.addWidget(self.trigger_level, 1, 1)
		self.trigger_box.setLayout(trig_layout)

		# --- Horizontal / timebase group ---
		self.horiz_box = QGroupBox("Horizontal")
		horiz_layout = QGridLayout()
		self.time_div = TrackedValue(
			bridge, "Time/div", get=lambda s: s.div_time, set_method="set_div_time",
			validator=QDoubleValidator(), unit="s")
		self.time_offset = TrackedValue(
			bridge, "Offset", get=lambda s: s.offset_time, set_method="set_offset_time",
			validator=QDoubleValidator(), unit="s")
		horiz_layout.addWidget(QLabel("Time/div:"), 0, 0)
		horiz_layout.addWidget(self.time_div, 0, 1)
		horiz_layout.addWidget(QLabel("Offset:"), 1, 0)
		horiz_layout.addWidget(self.time_offset, 1, 1)
		self.horiz_box.setLayout(horiz_layout)

		# --- Channels group - populated lazily in _build_channels() once the first state update
		# tells us how many channels this instrument actually has (state.num_channels). Doing
		# this from state rather than from a driver attribute is what keeps this widget working
		# identically whether `bridge` owns a local Driver or is only observing one over labmesh -
		# an ObserverBridge has no local Driver to read attributes off of at all. ---
		self.channels_box = QGroupBox("Channels")
		self.channels_layout = QHBoxLayout()
		self.channels_box.setLayout(self.channels_layout)

		# --- Waveform plot ---
		self.plot_widget = PlotWidget(main_window, log)

		self.main_layout.addWidget(self.acq_box, 0, 0, 1, 3)
		self.main_layout.addWidget(self.plot_widget, 1, 0, 1, 2)
		self.main_layout.addWidget(self.trigger_box, 1, 2)
		self.main_layout.addWidget(self.horiz_box, 2, 2)
		self.main_layout.addWidget(self.channels_box, 2, 0, 1, 2)
		self.setLayout(self.main_layout)

	def on_state_changed(self, state):

		if not self._channels_built:
			self._build_channels(state)

	def _build_channels(self, state):

		first = state.first_channel
		count = state.num_channels

		for ch in range(first, first + count):

			group = QGroupBox(f"Channel {ch}")
			layout = QGridLayout()

			enable = TrackedToggle(
				self.bridge, "Enable", get=(lambda s, ch=ch: s.channels[ch].chan_en),
				set_method="set_chan_enable", set_args=(lambda v, ch=ch: (ch, v)))
			vdiv = TrackedValue(
				self.bridge, "V/div", get=(lambda s, ch=ch: s.channels[ch].div_volt),
				set_method="set_div_volt", set_args=(lambda v, ch=ch: (ch, v)),
				validator=QDoubleValidator(), unit="V")
			voff = TrackedValue(
				self.bridge, "Offset", get=(lambda s, ch=ch: s.channels[ch].offset_volt),
				set_method="set_offset_volt", set_args=(lambda v, ch=ch: (ch, v)),
				validator=QDoubleValidator(), unit="V")
			coupling = TrackedChoice(
				self.bridge, "Coupling", get=(lambda s, ch=ch: s.channels[ch].coupling),
				set_method="set_coupling", set_args=(lambda v, ch=ch: (ch, v)),
				choices=[Oscilloscope.COUPLING_DC, Oscilloscope.COUPLING_AC, Oscilloscope.COUPLING_GND])

			layout.addWidget(enable, 0, 0, 1, 2)
			layout.addWidget(QLabel("V/div:"), 1, 0)
			layout.addWidget(vdiv, 1, 1)
			layout.addWidget(QLabel("Offset:"), 2, 0)
			layout.addWidget(voff, 2, 1)
			layout.addWidget(QLabel("Coupling:"), 3, 0)
			layout.addWidget(coupling, 3, 1)
			group.setLayout(layout)

			self.channels_layout.addWidget(group)
			self.channel_controls[ch] = {"enable": enable, "vdiv": vdiv, "voff": voff, "coupling": coupling}

		self._channels_built = True

	def _capture_waveforms(self):
		''' Fires one get_waveform request per channel - deliberately manual, see the class
		docstring for why this isn't folded into the automatic state refresh. '''

		for ch in self.channel_controls:
			self.bridge.request("get_waveform", ch)

	def _on_command_result(self, method_name, args, success, result):

		if method_name != "get_waveform" or not success:
			return

		ch = args[0] if args else None
		if ch is None or not isinstance(result, dict):
			return

		self._waveform_cache[ch] = result
		self._redraw_plot()

	def _redraw_plot(self):

		self.plot_widget.ax1a.cla()

		for ch, wav in self._waveform_cache.items():
			if "time_s" in wav and "volt_V" in wav:
				self.plot_widget.ax1a.plot(wav["time_s"], wav["volt_V"], label=f"Ch{ch}")

		self.plot_widget.ax1a.grid(True)
		self.plot_widget.ax1a.set_xlabel("Time [s]")
		self.plot_widget.ax1a.set_ylabel("Voltage [V]")
		if self._waveform_cache:
			self.plot_widget.ax1a.legend()

		self.plot_widget.fig1.tight_layout()
		self.plot_widget.fig1.canvas.draw_idle()
