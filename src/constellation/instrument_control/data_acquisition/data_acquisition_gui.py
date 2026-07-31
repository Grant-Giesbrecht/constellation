""" Reference GUI for the data-acquisition (DAQ) category.

Follows the same pattern as power_supply_gui.py / oscilloscope_gui.py (see docs/gui_authoring_guide.md):
Tracked* controls wired to the bridge for settings, plain buttons for actions, per-channel controls
built lazily from the first state update, and a plot for captured data.

DAQ-specific notes:
  - Configuration (sample rate, per-channel range/terminal-config/enable) has no hardware setpoint
    to read back - it lives in the driver's state - but Tracked* controls still work perfectly: the
    "confirmed" value is simply the state value echoed back on the next poll.
  - "Read Once" (single software-timed sample) and "Acquire" (buffered hardware-timed capture) are
    actions, not settings, so they are plain buttons calling bridge.request(...). Acquire can be
    slow, so - exactly like the oscilloscope's waveform capture - it is never folded into polling.
"""

from constellation.base import *
from constellation.instrument_control.data_acquisition.data_acquisition_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import QWidget, QGridLayout, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QGroupBox
from PyQt6.QtGui import QDoubleValidator

# Standard NI M-series symmetric input ranges (+/- volts). Offered as discrete choices because DAQ
# hardware only supports a fixed set of gains, not arbitrary ranges.
_STANDARD_RANGES_PM = [0.2, 1.0, 5.0, 10.0]

# How many channel group-boxes to place per row before wrapping.
_CHANNELS_PER_ROW = 4


@register_gui(DataAcquisition)
class DataAcquisitionWidget(InstrumentWidget):
	''' Front-panel-style DAQ GUI: a global acquisition-settings box, action buttons (Read Once /
	Acquire), a plot for the most recent buffered capture, and one box per analog-input channel
	(enable, terminal config, range, last reading). '''

	def __init__(self, main_window, bridge:InstrumentBridge, log:plf.LogPile):
		super().__init__(main_window, bridge, log)

		self._channels_built = False
		self.channel_controls = {}   # channel_num -> dict of controls + last-value label
		self._last_acq = None        # last acquire() result dict, for the plot

		bridge.command_result.connect(self._on_command_result)

		# --- Global acquisition settings (Tracked* - these are settings with a setpoint) ---
		self.settings_box = QGroupBox("Acquisition settings")
		settings_layout = QGridLayout()
		self.sample_rate = TrackedValue(
			bridge, "Sample rate", get=lambda s: s.sample_rate_Hz,
			set_method="set_sample_rate", validator=QDoubleValidator(), unit="Hz")
		self.samples = TrackedValue(
			bridge, "Samples/chan", get=lambda s: s.samples_per_channel,
			set_method="set_samples_per_channel", validator=QDoubleValidator())
		self.mode = TrackedChoice(
			bridge, "Mode", get=lambda s: s.acquisition_mode, set_method="set_acquisition_mode",
			choices=[DataAcquisition.MODE_FINITE, DataAcquisition.MODE_CONTINUOUS, DataAcquisition.MODE_ON_DEMAND])
		settings_layout.addWidget(QLabel("Sample rate:"), 0, 0)
		settings_layout.addWidget(self.sample_rate, 0, 1)
		settings_layout.addWidget(QLabel("Samples/chan:"), 1, 0)
		settings_layout.addWidget(self.samples, 1, 1)
		settings_layout.addWidget(QLabel("Mode:"), 2, 0)
		settings_layout.addWidget(self.mode, 2, 1)
		self.settings_box.setLayout(settings_layout)

		# --- Actions (plain buttons - no setpoint) ---
		self.actions_box = QGroupBox("Actions")
		actions_layout = QHBoxLayout()
		self.read_button = QPushButton("Read Once")
		self.read_button.clicked.connect(lambda: bridge.request("read_ai_single"))
		self.acquire_button = QPushButton("Acquire")
		self.acquire_button.clicked.connect(lambda: bridge.request("acquire"))
		actions_layout.addWidget(self.read_button)
		actions_layout.addWidget(self.acquire_button)
		self.actions_box.setLayout(actions_layout)

		# --- Plot of the most recent buffered acquisition ---
		self.plot_widget = PlotWidget(main_window, log)

		# --- Per-channel controls: built lazily from state.num_ai_channels ---
		self.channels_box = QGroupBox("Analog input channels")
		self.channels_layout = QGridLayout()
		self.channels_box.setLayout(self.channels_layout)

		self.main_layout.addWidget(self.settings_box, 0, 0)
		self.main_layout.addWidget(self.actions_box, 0, 1)
		self.main_layout.addWidget(self.plot_widget, 1, 0, 1, 2)
		self.main_layout.addWidget(self.channels_box, 2, 0, 1, 2)
		self.setLayout(self.main_layout)

	def on_state_changed(self, state):

		if not self._channels_built:
			self._build_channels(state)

		self._update_readings(state)

	def _build_channels(self, state):

		first = state.first_channel
		count = state.num_ai_channels

		for i, ch in enumerate(range(first, first + count)):

			group = QGroupBox(f"ai{ch}")
			layout = QGridLayout()

			# Bind the loop variable as a default argument in every closure (see gui_authoring_guide.md).
			enable = TrackedToggle(
				self.bridge, "Enable", get=(lambda s, ch=ch: s.ai_channels[ch].enabled),
				set_method="set_ai_channel_enabled", set_args=(lambda v, ch=ch: (ch, v)))
			term = TrackedChoice(
				self.bridge, "Terminal", get=(lambda s, ch=ch: s.ai_channels[ch].terminal_config),
				set_method="set_ai_channel_terminal_config", set_args=(lambda v, ch=ch: (ch, v)),
				choices=[DataAcquisition.TERM_DIFF, DataAcquisition.TERM_RSE,
					DataAcquisition.TERM_NRSE, DataAcquisition.TERM_PSEUDODIFF])
			rng = TrackedChoice(
				self.bridge, "Range", get=(lambda s, ch=ch: s.ai_channels[ch].range_max_V),
				set_method="set_ai_channel_range_pm", set_args=(lambda v, ch=ch: (ch, v)),
				choices=_STANDARD_RANGES_PM)

			value_label = QLabel("--")

			layout.addWidget(enable, 0, 0, 1, 2)
			layout.addWidget(QLabel("Terminal:"), 1, 0)
			layout.addWidget(term, 1, 1)
			layout.addWidget(QLabel("Range ±V:"), 2, 0)
			layout.addWidget(rng, 2, 1)
			layout.addWidget(QLabel("Last:"), 3, 0)
			layout.addWidget(value_label, 3, 1)
			group.setLayout(layout)

			row, col = divmod(i, _CHANNELS_PER_ROW)
			self.channels_layout.addWidget(group, row, col)
			self.channel_controls[ch] = {"enable": enable, "term": term, "range": rng, "value_label": value_label}

		self._channels_built = True

	def _update_readings(self, state):
		''' Read-only "last value" labels - a measurement has no setpoint, so it's a plain label
		updated straight from state, not a Tracked* control. '''
		for ch, controls in self.channel_controls.items():
			cs = state.ai_channels[ch]
			val = cs.last_value_V if cs is not None else None
			controls["value_label"].setText("--" if val is None else f"{val:.5g} V")

	def _on_command_result(self, method_name, args, success, result):

		if method_name == "read_ai_single" and success and isinstance(result, dict):
			# Reflect the fresh single-shot values immediately (don't wait for the next poll).
			for ch, v in result.items():
				if ch in self.channel_controls:
					self.channel_controls[ch]["value_label"].setText(f"{v:.5g} V")
			return

		if method_name == "acquire" and success and isinstance(result, dict) and result.get("channels"):
			self._last_acq = result
			self._redraw_plot()

	def _redraw_plot(self):

		self.plot_widget.ax1a.cla()

		acq = self._last_acq
		t = acq.get("t_s", [])
		for ch, samples in acq.get("channels", {}).items():
			n = min(len(t), len(samples))
			self.plot_widget.ax1a.plot(t[:n], samples[:n], label=f"ai{ch}")

		self.plot_widget.ax1a.grid(True)
		self.plot_widget.ax1a.set_xlabel("Time [s]")
		self.plot_widget.ax1a.set_ylabel("Voltage [V]")
		if acq.get("channels"):
			self.plot_widget.ax1a.legend()

		self.plot_widget.fig1.tight_layout()
		self.plot_widget.fig1.canvas.draw_idle()
