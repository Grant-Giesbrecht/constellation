""" Reference GUI for the power supply category - deliberately the minimal example. Copy this
file (not oscilloscope_gui.py) as the starting point for a new category's GUI - see
docs/gui_authoring_guide.md.
"""

from constellation.base import *
from constellation.instrument_control.power_supply.power_supply_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import QWidget, QGridLayout, QHBoxLayout, QLabel, QGroupBox
from PyQt6.QtGui import QDoubleValidator

@register_gui(PowerSupply)
class PowerSupplyWidget(InstrumentWidget):
	''' One QGroupBox per channel: output enable, voltage/current setpoints as TrackedControls,
	and measured voltage/current as plain read-only QLabels.

	The measured values are NOT TrackedValues - there's no setpoint for a measurement, only a
	reported number, so the pending/mismatch machinery doesn't apply. They're just updated
	straight from on_state_changed(). Unlike the oscilloscope's waveform capture, PowerSupply's
	refresh_state() already folds get_measured_output() into every poll (it's one quick query,
	not a multi-second transfer), so these labels update live with no capture button needed.
	'''

	def __init__(self, main_window, bridge:InstrumentBridge, log:plf.LogPile):
		super().__init__(main_window, bridge, log)

		self._channels_built = False
		self.channel_controls = {}  # channel_num -> dict of controls/labels

		self.channels_layout = QHBoxLayout()
		self.setLayout(self.channels_layout)

	def on_state_changed(self, state):

		if not self._channels_built:
			self._build_channels(state)

		self._update_measurements(state)

	def _build_channels(self, state):

		first = state.first_channel
		count = state.num_channels

		for ch in range(first, first + count):

			group = QGroupBox(f"Channel {ch}")
			layout = QGridLayout()

			enable = TrackedToggle(
				self.bridge, "Output", get=(lambda s, ch=ch: s.channels[ch].enable),
				set_method="set_output_enable", set_args=(lambda v, ch=ch: (ch, v)))
			voltage = TrackedValue(
				self.bridge, "Voltage", get=(lambda s, ch=ch: s.channels[ch].voltage_set),
				set_method="set_voltage", set_args=(lambda v, ch=ch: (ch, v)),
				validator=QDoubleValidator(), unit="V")
			current = TrackedValue(
				self.bridge, "Current limit", get=(lambda s, ch=ch: s.channels[ch].current_set),
				set_method="set_current", set_args=(lambda v, ch=ch: (ch, v)),
				validator=QDoubleValidator(), unit="A")

			meas_label = QLabel("--")

			layout.addWidget(enable, 0, 0, 1, 2)
			layout.addWidget(QLabel("Voltage:"), 1, 0)
			layout.addWidget(voltage, 1, 1)
			layout.addWidget(QLabel("Current limit:"), 2, 0)
			layout.addWidget(current, 2, 1)
			layout.addWidget(QLabel("Measured:"), 3, 0)
			layout.addWidget(meas_label, 3, 1)
			group.setLayout(layout)

			self.channels_layout.addWidget(group)
			self.channel_controls[ch] = {"enable": enable, "voltage": voltage, "current": current, "meas_label": meas_label}

		self._channels_built = True

	def _update_measurements(self, state):

		for ch, controls in self.channel_controls.items():
			chan_state = state.channels[ch]
			controls["meas_label"].setText(f"{chan_state.voltage_meas} V, {chan_state.current_meas} A")
