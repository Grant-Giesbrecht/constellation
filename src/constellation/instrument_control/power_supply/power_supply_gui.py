""" GUI for the power supply category - the smallest of the category GUIs, and the one to copy
first (see docs/gui_authoring_guide.md).

One collapsible panel per channel, side by side in a splitter. Settings - the output switch,
voltage and current setpoints, and the protection settings - are `Parameter*` controls. Two things
are deliberately not:

- **Readings** (measured voltage, current, power) have no setpoint to compare against, so they are
  read-only fields updated from on_state_changed(). They arrive with every poll: refresh_state()
  reads them, and each is one quick query.
- **Clearing a tripped protection** is an action, so it is a plain button, enabled only while the
  state says that protection has tripped.
"""

from constellation.base import *
from constellation.instrument_control.power_supply.power_supply_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import QWidget, QGridLayout, QVBoxLayout, QLabel, QLineEdit, QPushButton
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDoubleValidator

# Readings shown per channel: (state field, label, unit).
READINGS = (("voltage_meas", "Voltage", "V"), ("current_meas", "Current", "A"), ("power_meas", "Power", "W"))

# Protections shown per channel: (key used in method and state names, label, full name).
PROTECTIONS = (("ovp", "OVP", "Over-voltage protection"), ("ocp", "OCP", "Over-current protection"))

def format_reading(value) -> str:
	''' A reading as shown in its field; "--" until one has been taken. A tiny negative reading
	(noise around zero on an idle output) shows as 0 rather than "-0.0000". '''

	if value is None:
		return "--"

	text = f"{float(value):.4f}"
	return text[1:] if text == "-0.0000" else text

def _trip_dot_style(tripped) -> str:
	color = "#888888" if tripped is None else ("#e74c3c" if tripped else "#3a3a3a")
	return f"QLabel {{ background-color: {color}; border-radius: 5px; }}"

@register_gui(PowerSupply)
class PowerSupplyWidget(InstrumentWidget):

	def __init__(self, main_window, bridge:InstrumentBridge, log:plf.LogPile):
		super().__init__(main_window, bridge, log)

		self._channels_built = False
		self.channel_controls = {}  # channel_num -> {name: Parameter* control}
		self.readouts = {}          # channel_num -> {state field: read-only QLineEdit}
		self.trip_dots = {}         # channel_num -> {"ovp"/"ocp": QLabel}
		self.clear_buttons = {}     # channel_num -> {"ovp"/"ocp": QPushButton}
		self.channel_panels = {}    # channel_num -> CollapsiblePanel

		self.channels_splitter = make_splitter(Qt.Orientation.Horizontal)

		self.main_layout = QVBoxLayout()
		self.main_layout.addWidget(self.channels_splitter, 1)
		self.setLayout(self.main_layout)

	def on_state_changed(self, state):

		if not self._channels_built:
			self._build_channels(state)

		self._update_readings(state)

	def _build_channels(self, state):
		''' Built from `state`, never a driver attribute, so an ObserverBridge watching another
		process's supply gets the same panel. '''

		for ch in range(state.first_channel, state.first_channel + state.num_channels):

			controls = self._make_channel_controls(ch)

			layout = QVBoxLayout()
			layout.setContentsMargins(0, 0, 0, 0)

			layout.addLayout(self._column(controls["enable"], controls["voltage"], controls["current"]))
			layout.addLayout(self._readings_grid(ch))
			layout.addLayout(self._column(controls["ovp_enable"], controls["ovp_level"],
				controls["ocp_enable"], controls["ocp_level"]))
			layout.addLayout(self._trips_grid(ch))
			layout.addStretch(1)

			panel = CollapsiblePanel(f"Channel {ch}", fold=Qt.Orientation.Horizontal)
			panel.set_content_layout(layout)
			self.channels_splitter.addWidget(panel)

			self.channel_controls[ch] = controls
			self.channel_panels[ch] = panel

		self._channels_built = True

	def _make_channel_controls(self, ch:int) -> dict:
		''' One channel's settings. Closures bind `ch` as a default - the loop-variable trap the
		authoring guide warns about. '''

		def args(v, ch=ch):
			return (ch, v)

		return {
			"enable": ParameterToggle(
				self.bridge, f"Output {ch}", get=(lambda s, ch=ch: s.channels[ch].enable),
				set_method="set_output_enable", set_args=args, get_args=(ch,)),
			"voltage": ParameterBox(
				self.bridge, "Voltage", get=(lambda s, ch=ch: s.channels[ch].voltage_set),
				set_method="set_voltage", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="V", abs_tolerance=1e-3),
			"current": ParameterBox(
				self.bridge, "Current limit", get=(lambda s, ch=ch: s.channels[ch].current_set),
				set_method="set_current", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="A", abs_tolerance=1e-3, prefixes=("", "m")),
			"ovp_enable": ParameterToggle(
				self.bridge, "OVP", get=(lambda s, ch=ch: s.channels[ch].ovp_enable),
				set_method="set_ovp_enable", set_args=args, get_args=(ch,)),
			"ovp_level": ParameterBox(
				self.bridge, "OVP level", get=(lambda s, ch=ch: s.channels[ch].ovp_level),
				set_method="set_ovp_level", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="V", abs_tolerance=1e-3),
			"ocp_enable": ParameterToggle(
				self.bridge, "OCP", get=(lambda s, ch=ch: s.channels[ch].ocp_enable),
				set_method="set_ocp_enable", set_args=args, get_args=(ch,)),
			"ocp_level": ParameterBox(
				self.bridge, "OCP level", get=(lambda s, ch=ch: s.channels[ch].ocp_level),
				set_method="set_ocp_level", set_args=args, get_args=(ch,),
				validator=QDoubleValidator(), unit="A", abs_tolerance=1e-3, prefixes=("", "m")),
		}

	@staticmethod
	def _column(*controls) -> QGridLayout:
		''' Controls stacked one per row, with slack landing below and beside them rather than
		inside them. '''

		grid = QGridLayout()
		grid.setContentsMargins(0, 0, 0, 0)
		for row, control in enumerate(controls):
			grid.addWidget(control, row, 0)
		grid.setColumnStretch(1, 1)
		return grid

	def _readings_grid(self, ch:int) -> QGridLayout:

		grid = QGridLayout()
		grid.setContentsMargins(4, 4, 4, 4)
		self.readouts[ch] = {}

		for row, (field, label, unit) in enumerate(READINGS):

			readout = QLineEdit(format_reading(None))
			readout.setReadOnly(True)
			readout.setFocusPolicy(Qt.FocusPolicy.NoFocus)
			readout.setFixedWidth(90)
			readout.setToolTip(f"Measured {label.lower()} at the output terminals")

			grid.addWidget(QLabel(f"Measured {label.lower()}:"), row, 0)
			grid.addWidget(readout, row, 1)
			grid.addWidget(QLabel(unit), row, 2)
			self.readouts[ch][field] = readout

		grid.setColumnStretch(3, 1)
		return grid

	def _trips_grid(self, ch:int) -> QGridLayout:

		grid = QGridLayout()
		grid.setContentsMargins(4, 4, 4, 4)
		self.trip_dots[ch] = {}
		self.clear_buttons[ch] = {}

		for row, (key, label, full_name) in enumerate(PROTECTIONS):

			dot = QLabel()
			dot.setFixedSize(10, 10)
			dot.setStyleSheet(_trip_dot_style(None))

			clear = QPushButton("Clear")
			clear.setEnabled(False)
			clear.setToolTip(f"Clear the tripped {full_name.lower()}. Remove the cause first, or it trips again.")
			clear.clicked.connect(lambda checked=False, key=key, ch=ch: self.bridge.request(f"clear_{key}_trip", ch))

			grid.addWidget(dot, row, 0)
			grid.addWidget(QLabel(f"{label} tripped"), row, 1)
			grid.addWidget(clear, row, 2)

			self.trip_dots[ch][key] = dot
			self.clear_buttons[ch][key] = clear

		grid.setColumnStretch(3, 1)
		return grid

	def _update_readings(self, state):

		for ch, readouts in self.readouts.items():

			chan = state.channels[ch]

			for field, readout in readouts.items():
				readout.setText(format_reading(getattr(chan, field)))

			for key, label, full_name in PROTECTIONS:

				tripped = getattr(chan, f"{key}_tripped")

				dot = self.trip_dots[ch][key]
				dot.setStyleSheet(_trip_dot_style(tripped))
				dot.setToolTip({None: f"{full_name}: not read yet", True: f"{full_name} has tripped",
					False: f"{full_name} has not tripped"}[None if tripped is None else bool(tripped)])

				self.clear_buttons[ch][key].setEnabled(bool(tripped))
