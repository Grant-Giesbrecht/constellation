""" GUI for the power supply category - the smallest of the category GUIs, and the one to copy
first (see docs/gui_authoring_guide.md).

One collapsible panel per channel, side by side in a splitter. Each holds three sections:

- **Set Points** (a framed box): the output switch and the voltage and current setpoints, as
  `Parameter*` controls.
- **Measured Output** (a framed box): measured voltage, current and power. These have no setpoint
  to compare against, so they are readouts rather than `Parameter*` controls - LCD displays by
  default, switchable to read-only text fields from the View menu. They arrive with every poll:
  refresh_state() reads them, and each is one quick query.
- **Protection** (a collapsible panel, so a channel that doesn't need it can fold it away): OVP and
  OCP settings, and a tripped lamp per protection. Clearing a trip is an action, so it is a plain
  button, enabled only while the state says that protection has tripped.
"""

from constellation.base import *
from constellation.instrument_control.power_supply.power_supply_ctg import *
from constellation.ui import *

from PyQt6.QtWidgets import (QWidget, QGridLayout, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
	QPushButton, QGroupBox, QLCDNumber)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDoubleValidator, QAction

# Readings shown per channel: (state field, label, unit).
READINGS = (("voltage_meas", "Voltage", "V"), ("current_meas", "Current", "A"), ("power_meas", "Power", "W"))

# Protections shown per channel: (key used in method and state names, label, full name).
PROTECTIONS = (("ovp", "OVP", "Over-voltage protection"), ("ocp", "OCP", "Over-current protection"))

# LCD appearance defaults - see PowerSupplyWidget.set_lcd_appearance(). Height in pixels; colours
# are any Qt colour string, and None keeps the palette's.
LCD_DIGITS = 7
LCD_HEIGHT = 36
LCD_COLOR = None
LCD_BACKGROUND = None

def format_reading(value) -> str:
	''' A reading as shown; "--" until one has been taken. A tiny negative reading (noise around
	zero on an idle output) shows as 0 rather than "-0.0000". '''

	if value is None:
		return "--"

	text = f"{float(value):.4f}"
	return text[1:] if text == "-0.0000" else text

def _trip_dot_style(tripped) -> str:
	color = "#888888" if tripped is None else ("#e74c3c" if tripped else "#3a3a3a")
	return f"QLabel {{ background-color: {color}; border-radius: 5px; }}"

class Readout(QWidget):
	''' One measured value, shown on an LCD or in a read-only text field. Both widgets are built
	once and one is shown, so switching never loses the value. '''

	def __init__(self, tooltip:str="", lcd:bool=True, parent=None):
		super().__init__(parent)

		self._text = format_reading(None)

		self.field = QLineEdit(self._text)
		self.field.setReadOnly(True)
		self.field.setFocusPolicy(Qt.FocusPolicy.NoFocus)
		self.field.setFixedWidth(90)
		self.field.setToolTip(tooltip)

		self.lcd = QLCDNumber()
		self.lcd.setDigitCount(LCD_DIGITS)
		self.lcd.setSegmentStyle(QLCDNumber.SegmentStyle.Flat)
		self.lcd.setSmallDecimalPoint(True)
		self.lcd.setToolTip(tooltip)
		self.lcd.display(self._text)

		self.set_lcd_appearance(height=LCD_HEIGHT, color=LCD_COLOR, background=LCD_BACKGROUND)

		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.addWidget(self.field)
		layout.addWidget(self.lcd)
		self.setLayout(layout)

		self._lcd = None
		self.set_lcd(lcd)

	@property
	def is_lcd(self) -> bool:
		return self._lcd

	def text(self) -> str:
		return self._text

	def set_lcd(self, enabled:bool):
		self._lcd = bool(enabled)
		self.lcd.setVisible(self._lcd)
		self.field.setVisible(not self._lcd)

	def set_value(self, value):
		self._text = format_reading(value)
		self.field.setText(self._text)
		self.lcd.display(self._text)

	def set_lcd_appearance(self, height:int=None, color:str=None, background:str=None):
		''' Any argument left as None is left as it is. The width follows the height, so the digits
		keep their proportions. '''

		if height is not None:
			self.lcd.setFixedSize(int(height * LCD_DIGITS * 0.45), int(height))

		if color is not None or background is not None:
			rules = []
			if color is not None:
				rules.append(f"color: {color};")
			if background is not None:
				rules.append(f"background-color: {background};")
			self.lcd.setStyleSheet(f"QLCDNumber {{ {' '.join(rules)} }}")

@register_gui(PowerSupply)
class PowerSupplyWidget(InstrumentWidget):

	def __init__(self, main_window, bridge:InstrumentBridge, log:plf.LogPile):
		super().__init__(main_window, bridge, log)

		self._channels_built = False
		self.channel_controls = {}    # channel_num -> {name: Parameter* control}
		self.readouts = {}            # channel_num -> {state field: Readout}
		self.trip_dots = {}           # channel_num -> {"ovp"/"ocp": QLabel}
		self.clear_buttons = {}       # channel_num -> {"ovp"/"ocp": QPushButton}
		self.channel_panels = {}      # channel_num -> CollapsiblePanel
		self.setpoint_boxes = {}      # channel_num -> QGroupBox
		self.measured_boxes = {}      # channel_num -> QGroupBox
		self.protection_panels = {}   # channel_num -> CollapsiblePanel

		# Readout style: LCDs by default, changed from the View menu. Appearance is kept here too,
		# so readouts built lazily from the first state pick it up.
		self.readings_lcd = True
		self._lcd_appearance = {}
		self._lcd_action = None

		self.channels_splitter = make_splitter(Qt.Orientation.Horizontal)

		self.main_layout = QVBoxLayout()
		self.main_layout.addWidget(self.channels_splitter, 1)
		self.setLayout(self.main_layout)

	def on_state_changed(self, state):

		if not self._channels_built:
			self._build_channels(state)

		self._update_readings(state)

	# --- construction ----------------------------------------------------------------------

	def _build_channels(self, state):
		''' Built from `state`, never a driver attribute, so an ObserverBridge watching another
		process's supply gets the same panel. '''

		for ch in range(state.first_channel, state.first_channel + state.num_channels):

			controls = self._make_channel_controls(ch)

			setpoints = QGroupBox("Set Points")
			setpoints.setLayout(self._column(controls["enable"], controls["voltage"], controls["current"]))

			measured = QGroupBox("Measured Output")
			measured.setLayout(self._readings_grid(ch))

			protection_layout = QVBoxLayout()
			protection_layout.setContentsMargins(0, 0, 0, 0)
			protection_layout.addLayout(self._column(controls["ovp_enable"], controls["ovp_level"],
				controls["ocp_enable"], controls["ocp_level"]))
			protection_layout.addLayout(self._trips_grid(ch))

			# Folds vertically, whatever the splitter around the channel panel does: it sits in a
			# column inside one channel, and folding gives that column back its height.
			protection = CollapsiblePanel("Protection", fold=Qt.Orientation.Vertical)
			protection.set_content_layout(protection_layout)

			layout = QVBoxLayout()
			layout.setContentsMargins(0, 0, 0, 0)
			layout.addWidget(setpoints)
			layout.addWidget(measured)
			layout.addWidget(protection)
			layout.addStretch(1)

			panel = CollapsiblePanel(f"Channel {ch}", fold=Qt.Orientation.Horizontal)
			panel.set_content_layout(layout)
			self.channels_splitter.addWidget(panel)

			self.channel_controls[ch] = controls
			self.channel_panels[ch] = panel
			self.setpoint_boxes[ch] = setpoints
			self.measured_boxes[ch] = measured
			self.protection_panels[ch] = protection

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
		''' Controls stacked one per row, with slack landing beside them rather than inside them. '''

		grid = QGridLayout()
		grid.setContentsMargins(4, 4, 4, 4)
		for row, control in enumerate(controls):
			grid.addWidget(control, row, 0)
		grid.setColumnStretch(1, 1)
		return grid

	def _readings_grid(self, ch:int) -> QGridLayout:

		grid = QGridLayout()
		grid.setContentsMargins(4, 4, 4, 4)
		self.readouts[ch] = {}

		for row, (field, label, unit) in enumerate(READINGS):

			readout = Readout(tooltip=f"Measured {label.lower()} at the output terminals", lcd=self.readings_lcd)
			if self._lcd_appearance:
				readout.set_lcd_appearance(**self._lcd_appearance)

			grid.addWidget(QLabel(label), row, 0)
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

	# --- readouts ----------------------------------------------------------------------------

	def set_readings_lcd(self, enabled:bool):
		''' Shows every reading on an LCD (True) or in a read-only text field (False). '''

		self.readings_lcd = bool(enabled)

		for readouts in self.readouts.values():
			for readout in readouts.values():
				readout.set_lcd(self.readings_lcd)

		# Keep the View menu's tick in step when this is changed from code. The action belongs to a
		# menu that is rebuilt whenever an instrument is added, so it may already be gone.
		try:
			if self._lcd_action is not None and self._lcd_action.isChecked() != self.readings_lcd:
				self._lcd_action.setChecked(self.readings_lcd)
		except RuntimeError:
			self._lcd_action = None

	def set_lcd_appearance(self, height:int=None, color:str=None, background:str=None):
		''' Resizes or recolours every reading's LCD, including ones built later. `height` is in
		pixels (the width follows); `color` and `background` are any Qt colour string. Arguments
		left as None are unchanged. '''

		for name, value in (("height", height), ("color", color), ("background", background)):
			if value is not None:
				self._lcd_appearance[name] = value

		for readouts in self.readouts.values():
			for readout in readouts.values():
				readout.set_lcd_appearance(height=height, color=color, background=background)

	def has_view_actions(self) -> bool:
		return True

	def add_view_actions(self, menu) -> None:
		''' Rebuilt with the menu, so the action is created fresh each time and ticked from the
		current setting. '''

		action = QAction("Readings as LCD Displays", menu)
		action.setCheckable(True)
		action.setChecked(self.readings_lcd)
		action.triggered.connect(self.set_readings_lcd)
		menu.addAction(action)
		self._lcd_action = action

	def _update_readings(self, state):

		for ch, readouts in self.readouts.items():

			chan = state.channels[ch]

			for field, readout in readouts.items():
				readout.set_value(getattr(chan, field))

			for key, label, full_name in PROTECTIONS:

				tripped = getattr(chan, f"{key}_tripped")

				dot = self.trip_dots[ch][key]
				dot.setStyleSheet(_trip_dot_style(tripped))
				dot.setToolTip({None: f"{full_name}: not read yet", True: f"{full_name} has tripped",
					False: f"{full_name} has not tripped"}[None if tripped is None else bool(tripped)])

				self.clear_buttons[ch][key].setEnabled(bool(tripped))
