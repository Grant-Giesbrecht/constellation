""" Reference GUI for the oscilloscope category.

Read this file alongside docs/gui_architecture_proposal.md and docs/gui_authoring_guide.md if
you're building a GUI for a different category - the pattern here (Tracked* controls wired to a
bridge, lazy per-channel construction from the first state update, front-panel-style grouping) is
meant to be copied, not reinvented per category.

Controls are `Parameter*` in their COMPACT view: each one carries its own label, its own lamps,
and its own detail window, so this file places one widget per setting rather than a label plus a
control plus hand-placed indicators. Click any lamp to turn a control up to FULL and see the
instrument's own reported value next to the setpoint.
"""

from constellation.base import *
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import (QWidget, QGridLayout, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
	QGroupBox, QSplitter, QMessageBox)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDoubleValidator
from constellation.widgets import StatusPushButton

# class ChannelWidget(QWidget):
# 	
# 	def __init__(self, main_window, driver:Driver, log:plf.LogPile, channel_num:int):
# 		super().__init__(main_window)
# 		
# 		self.main_window = main_window
# 		self.driver = driver
# 		self.log = log
# 		self.channel_num = channel_num
# 		
# 		self.main_layout = QGridLayout()
# 		
# 		self.chan_label = QLabel(f"Channel {channel_num}")
# 		
# 		self.enable_button = StatusPushButton("Enable", parent=self)
# 		# self.enable_button.setCheckable(True)
# 		
# 		self.vdiv_label = QLabel("Volts/div [V]:")
# 		self.vdiv_edit = QLineEdit()
# 		self.vdiv_edit.setValidator(QDoubleValidator())
# 		vdiv_val = self.driver.state.channels[self.channel_num].div_volt
# 		self.vdiv_edit.setText(f"{vdiv_val}")
# 		self.vdiv_edit.setFixedWidth(80)
# 		
# 		self.voff_label = QLabel("Voltage offset [V]:")
# 		self.voff_edit = QLineEdit()
# 		self.voff_edit.setValidator(QDoubleValidator())
# 		
# 		temp = self.driver.state.channels[self.channel_num].offset_volt
# 		self.voff_edit.setText(f"{temp}")
# 		self.voff_edit.setFixedWidth(80)
# 		
# 		self.main_layout.addWidget(self.chan_label, 0, 0, 1, 2)
# 		self.main_layout.addWidget(self.enable_button, 1, 0, 1, 2)
# 		self.main_layout.addWidget(self.vdiv_label, 2, 0)
# 		self.main_layout.addWidget(self.vdiv_edit, 2, 1)
# 		self.main_layout.addWidget(self.voff_label, 3, 0)
# 		self.main_layout.addWidget(self.voff_edit, 3, 1)
# 		
# 		# self.xmin_edit.editingFinished.connect(self.apply_changes)
# 		
# 		self.setLayout(self.main_layout)
# 	
# 	def state_to_ui(self):
# 		
# 		self.log.lowdebug(f"Channel {self.channel_num} updating UI from state.")
# 		
# 		try:
# 			self.enable_button.set_status( self.driver.state.channels[self.channel_num].chan_en )
# 			self.vdiv_edit.setText(str( self.driver.state.channels[self.channel_num].div_volt ))
# 			self.voff_edit.setText(str( self.driver.state.channels[self.channel_num].offset_volt ))
# 		except Exception as e:
# 			self.log.warning(f"state_to_ui() failed: {e}")

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
		self.channel_controls = {}  # channel_num -> dict of Parameter* controls, for anyone who wants to reach in
		self._waveform_cache = {}   # channel_num -> last captured waveform dict

		bridge.command_result.connect(self._on_command_result)

		# --- Acquisition controls: deliberately plain buttons, not Parameter* - these are actions
		# (run/stop/single/capture), not settings with a confirmable setpoint. ---
		self.run_button = QPushButton("Run")
		self.run_button.clicked.connect(lambda: bridge.request("run_acquisition"))
		self.stop_button = QPushButton("Stop")
		self.stop_button.clicked.connect(lambda: bridge.request("stop_acquisition"))
		self.single_button = QPushButton("Single")
		self.single_button.clicked.connect(lambda: bridge.request("do_single_trigger"))
		self.capture_button = QPushButton("Capture Waveforms")
		self.capture_button.clicked.connect(self._capture_waveforms)

		# Stacked, not in a row: this panel now lives in the narrow sidebar alongside Trigger and
		# Horizontal, where four side-by-side buttons would each be too narrow to read.
		self.acq_box = CollapsiblePanel("Acquisition")
		acq_layout = QVBoxLayout()
		acq_layout.setContentsMargins(0, 0, 0, 0)
		for b in (self.run_button, self.stop_button, self.single_button, self.capture_button):
			acq_layout.addWidget(b)
		self.acq_box.set_content_layout(acq_layout)

		# --- Trigger group ---
		self.trigger_box = CollapsiblePanel("Trigger")
		trig_layout = QGridLayout()
		trig_layout.setContentsMargins(0, 0, 0, 0)
		self.trigger_mode = ParameterChoice(
			bridge, "Mode", get=lambda s: s.trigger_mode, set_method="set_trigger_mode",
			choices=[Oscilloscope.TRIG_AUTO, Oscilloscope.TRIG_NORM, Oscilloscope.TRIG_SINGLE],
			labels={Oscilloscope.TRIG_AUTO: "AUTO", Oscilloscope.TRIG_NORM: "NORMAL", Oscilloscope.TRIG_SINGLE: "SINGLE"})
		self.trigger_level = ParameterBox(
			bridge, "Level", get=lambda s: s.trigger_level, set_method="set_trigger_level",
			validator=QDoubleValidator(), unit="V", abs_tolerance=0.01, prefixes=("", "m"))
		trig_layout.addWidget(self.trigger_mode, 0, 0)
		trig_layout.addWidget(self.trigger_level, 1, 0)
		# Controls keep their natural width and stay left; the slack goes into the empty column.
		trig_layout.setColumnStretch(1, 1)
		trig_layout.setRowStretch(2, 1)
		self.trigger_box.set_content_layout(trig_layout)

		# --- Horizontal / timebase group ---
		self.horiz_box = CollapsiblePanel("Horizontal")
		horiz_layout = QGridLayout()
		horiz_layout.setContentsMargins(0, 0, 0, 0)
		# Timebase values are microseconds to milliseconds in practice, so both of these carry a
		# prefix selector: a user types "2" and picks "ms" rather than counting zeros in 0.002.
		self.time_div = ParameterBox(
			bridge, "Time/div", get=lambda s: s.div_time, set_method="set_div_time",
			validator=QDoubleValidator(), unit="s", prefixes=("", "m", "\u00b5", "n"))
		self.time_offset = ParameterBox(
			bridge, "Offset", get=lambda s: s.offset_time, set_method="set_offset_time",
			validator=QDoubleValidator(), unit="s", abs_tolerance=1e-9,
			prefixes=("", "m", "\u00b5", "n"))
		horiz_layout.addWidget(self.time_div, 0, 0)
		horiz_layout.addWidget(self.time_offset, 1, 0)
		horiz_layout.setColumnStretch(1, 1)
		horiz_layout.setRowStretch(2, 1)
		self.horiz_box.set_content_layout(horiz_layout)

		# --- Channels group - populated lazily in _build_channels() once the first state update
		# tells us how many channels this instrument actually has (state.num_channels). Doing
		# this from state rather than from a driver attribute is what keeps this widget working
		# identically whether `bridge` owns a local Driver or is only observing one over labmesh -
		# an ObserverBridge has no local Driver to read attributes off of at all. ---
		self.channels_box = CollapsiblePanel("Channels")
		self.channels_splitter = make_splitter(Qt.Orientation.Horizontal)
		channels_layout = QHBoxLayout()
		channels_layout.setContentsMargins(0, 0, 0, 0)
		channels_layout.addWidget(self.channels_splitter)
		self.channels_box.set_content_layout(channels_layout)

		# --- Waveform plot, in its own foldable panel with its own actions ---
		self.plot_widget = PlotWidget(main_window, log)

		self.save_trace_button = QPushButton("Save Trace...")
		self.save_trace_button.clicked.connect(self._save_trace)

		wave_actions = QHBoxLayout()
		wave_actions.setContentsMargins(0, 0, 0, 0)
		wave_actions.addWidget(self.save_trace_button)
		wave_actions.addStretch(1)

		wave_layout = QVBoxLayout()
		wave_layout.setContentsMargins(0, 0, 0, 0)
		wave_layout.addLayout(wave_actions)
		wave_layout.addWidget(self.plot_widget, 1)

		self.waveform_box = CollapsiblePanel("Waveform")
		self.waveform_box.set_content_layout(wave_layout)

		# --- Assembly ---
		# Nested splitters rather than a fixed grid, so a user can give the plot more room, shrink
		# the channel strip, or fold a panel away entirely. Every section is a CollapsiblePanel,
		# and they fold INDEPENDENTLY: folding all three side panels leaves the waveform showing
		# and hands it the space, and folding the waveform leaves the side panels alone.
		self.side_splitter = make_splitter(Qt.Orientation.Vertical, self.acq_box,
			self.trigger_box, self.horiz_box)
		self.upper_splitter = make_splitter(Qt.Orientation.Horizontal, self.waveform_box,
			self.side_splitter, stretch=[3, 1])
		self.body_splitter = make_splitter(Qt.Orientation.Vertical, self.upper_splitter,
			self.channels_box, stretch=[4, 1])

		self.main_layout = QVBoxLayout()
		self.main_layout.addWidget(self.body_splitter, 1)
		self.setLayout(self.main_layout)

	def on_state_changed(self, state):

		if not self._channels_built:
			self._build_channels(state)

	def _build_channels(self, state):

		first = state.first_channel
		count = state.num_channels

		for ch in range(first, first + count):

			# Folds sideways: these sit in a row, so collapsing one should give its width to its
			# neighbours rather than leaving an empty column.
			group = CollapsiblePanel(f"Channel {ch}", fold=Qt.Orientation.Horizontal)
			layout = QGridLayout()
			layout.setContentsMargins(0, 0, 0, 0)

			# get_args are the *getter's* arguments, which is what the PV button re-queries with -
			# same channel number, but the getter takes it alone rather than alongside a value.
			enable = ParameterToggle(
				self.bridge, f"Channel {ch}", get=(lambda s, ch=ch: s.channels[ch].chan_en),
				set_method="set_chan_enable", set_args=(lambda v, ch=ch: (ch, v)), get_args=(ch,))
			vdiv = ParameterBox(
				self.bridge, "V/div", get=(lambda s, ch=ch: s.channels[ch].div_volt),
				set_method="set_div_volt", set_args=(lambda v, ch=ch: (ch, v)), get_args=(ch,),
				validator=QDoubleValidator(), unit="V", prefixes=("", "m"))
			voff = ParameterBox(
				self.bridge, "Offset", get=(lambda s, ch=ch: s.channels[ch].offset_volt),
				set_method="set_offset_volt", set_args=(lambda v, ch=ch: (ch, v)), get_args=(ch,),
				validator=QDoubleValidator(), unit="V", abs_tolerance=0.01, prefixes=("", "m"))
			coupling = ParameterChoice(
				self.bridge, "Coupling", get=(lambda s, ch=ch: s.channels[ch].coupling),
				set_method="set_coupling", set_args=(lambda v, ch=ch: (ch, v)), get_args=(ch,),
				choices=[Oscilloscope.COUPLING_DC, Oscilloscope.COUPLING_AC, Oscilloscope.COUPLING_GND],
				labels={Oscilloscope.COUPLING_DC: "DC", Oscilloscope.COUPLING_AC: "AC", Oscilloscope.COUPLING_GND: "GND"})

			for row, control in enumerate((enable, vdiv, voff, coupling)):
				layout.addWidget(control, row, 0)

			layout.setColumnStretch(1, 1)
			layout.setRowStretch(4, 1)
			group.set_content_layout(layout)

			self.channels_splitter.addWidget(group)
			self.channel_controls[ch] = {"enable": enable, "vdiv": vdiv, "voff": voff, "coupling": coupling}

		self._channels_built = True

	def _traces(self) -> list:
		''' The captured waveforms as generic Trace records, for the export dialog.

		Tolerates both spellings of the time key: the category seeds `waveform` as
		{"time_S", "volt_V"} while RigolDS1000Z returns {"time_s", ...}. The data-shape contract
		is a known open item (P11) - until it is settled, exporting should not fail over a capital
		letter.
		'''

		traces = []

		for channel in sorted(self._waveform_cache):

			wave = self._waveform_cache[channel] or {}

			x = next((wave[k] for k in ("time_s", "time_S", "x") if wave.get(k) is not None), None)
			y = next((wave[k] for k in ("volt_V", "y") if wave.get(k) is not None), None)

			if y is None or not len(y):
				continue

			traces.append(Trace(f"Channel {channel}", x if x is not None else range(len(y)), y,
				x_unit="s", y_unit="V", metadata={"channel": channel}))

		return traces

	def _save_trace(self):

		traces = self._traces()

		if not traces:
			QMessageBox.information(self, "Nothing to save",
				"No waveforms have been captured yet - use \"Capture Waveforms\" first.")
			return

		dialog = SaveTraceDialog(traces, figure=self.plot_widget.fig1,
			metadata={"instrument": getattr(self, "panel_title", "oscilloscope"),
				"description": "Captured with Constellation"},
			parent=self, log=self.log)

		dialog.exec()

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
