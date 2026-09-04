""" The SP/PV parameter controls, against a dummy oscilloscope.

Runs with no instrument attached. What it demonstrates is the thing a single-box control cannot
show: what you asked for and what the instrument reports, side by side, with three independent
lamps saying whether the method can be trusted at all, whether the command got there, and whether
the instrument agrees - plus the density switch that lets you show as little or as much of that
as a panel deserves.

	python examples/parameter_widgets_demo.py

Things to try once it's up:

  - Click any lamp. The detail window explains all three, shows the last value sent and received
    and the SCPI behind them, and has a "Detail shown" dropdown that changes that control's
    density live - minimal, compact, or full.
  - The three panels are the same controls at the three densities, so you can see what each one
    costs and buys.
  - Set volts/div to 0.55. A real scope quantizes to 0.5 - the PV row shows the instrument's
    answer next to yours and the bottom lamp goes GREY, not red. Grey means "these differ, look at
    them", which is usually the instrument snapping to its own grid rather than anything wrong.
  - Mouse over the top lamp. Everything here is yellow/untested, because no method in this driver
    has been checked against real hardware yet (see docs/hardware_verification.md). The tooltip
    names both halves of the set/get pair and the weaker one is what the lamp shows.
  - Click the PV icon to re-read a value, or SP to re-send the current setpoint.
  - Hit "Simulate a failed send" to see the middle lamp go red with the error in its tooltip, and
    "Simulate going offline" to see the bottom lamp separate "cannot read" (red) from "read
    something that disagrees" (grey).

The same panel is built against a RigolDS1000E further down. That driver genuinely cannot set its
timebase over SCPI, so its Time/div control is dark and dead rather than raising
FeatureUnavailable when you touch it.
"""

import sys

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QGroupBox, QGridLayout, QVBoxLayout, QHBoxLayout, QPushButton, QLabel

from constellation.all import *
from constellation.ui import (OwningBridge, ParameterBox, ParameterToggle, ParameterChoice,
	ParameterView)
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import Oscilloscope

CHANNEL = 1

def build_panel(bridge, title:str, view:str=ParameterView.FULL) -> QGroupBox:
	''' One instrument's worth of controls. Nothing here is scope-specific machinery - each
	control is one `get` lambda plus the driver method name it drives. '''

	box = QGroupBox(title)
	grid = QGridLayout()

	controls = [
		ParameterBox(bridge, "Time/div", get=lambda s: s.div_time,
			set_method="set_div_time", set_args=lambda v: (v,), unit="s"),

		ParameterBox(bridge, "Volts/div", get=lambda s: s.channels[CHANNEL].div_volt,
			set_method="set_div_volt", set_args=lambda v: (CHANNEL, v), get_args=(CHANNEL,), unit="V"),

		ParameterBox(bridge, "Offset", get=lambda s: s.channels[CHANNEL].offset_volt,
			set_method="set_offset_volt", set_args=lambda v: (CHANNEL, v), get_args=(CHANNEL,),
			unit="V", abs_tolerance=0.01),

		ParameterToggle(bridge, f"Channel {CHANNEL}", get=lambda s: s.channels[CHANNEL].chan_en,
			set_method="set_chan_enable", set_args=lambda v: (CHANNEL, v), get_args=(CHANNEL,)),

		ParameterChoice(bridge, "Coupling", get=lambda s: s.channels[CHANNEL].coupling,
			set_method="set_coupling", set_args=lambda v: (CHANNEL, v), get_args=(CHANNEL,),
			choices=[Oscilloscope.COUPLING_AC, Oscilloscope.COUPLING_DC, Oscilloscope.COUPLING_GND],
			labels={Oscilloscope.COUPLING_AC: "AC", Oscilloscope.COUPLING_DC: "DC", Oscilloscope.COUPLING_GND: "GND"}),

		ParameterChoice(bridge, "Trigger mode", get=lambda s: s.trigger_mode,
			set_method="set_trigger_mode", set_args=lambda v: (v,),
			choices=[Oscilloscope.TRIG_AUTO, Oscilloscope.TRIG_NORM, Oscilloscope.TRIG_SINGLE],
			labels={Oscilloscope.TRIG_AUTO: "AUTO", Oscilloscope.TRIG_NORM: "NORMAL", Oscilloscope.TRIG_SINGLE: "SINGLE"}),
	]

	for control in controls:
		control.set_view(view)

	for i, control in enumerate(controls):
		grid.addWidget(control, i // 3, i % 3)

	# Extra width goes into the gaps between controls, not into the controls themselves.
	grid.setColumnStretch(3, 1)

	box.setLayout(grid)
	box.controls = controls

	return box

def build_fault_buttons(bridge, panel) -> QWidget:
	''' Fires the signals a real bridge emits on failure, so the lamps can be seen doing their job
	without unplugging anything. '''

	row = QHBoxLayout()
	row.addWidget(QLabel("Simulate:"))

	def fail_send():
		for control in panel.controls:
			bridge.command_result.emit(control.set_method, (), False, RuntimeError("VI_ERROR_TMO: timeout expired"))

	def go_offline():
		bridge.connection_changed.emit(False)

	def go_online():
		bridge.connection_changed.emit(True)

	for text, slot in (("a failed send", fail_send), ("going offline", go_offline), ("coming back", go_online)):
		button = QPushButton(text)
		button.clicked.connect(slot)
		row.addWidget(button)

	row.addStretch(1)

	holder = QWidget()
	holder.setLayout(row)

	return holder

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.WARNING

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")

window = QtWidgets.QMainWindow()
window.setWindowTitle("Parameter controls - SP/PV demo")

layout = QVBoxLayout()

# A DS1000Z: everything implemented, nothing verified against hardware yet, so every top lamp is
# yellow. That is the honest state of this repository today.
scope = RigolDS1000Z("DUMMY", log=log, dummy=True)
bridge = OwningBridge(scope, poll_interval_s=1.0)
panel = build_panel(bridge, "FULL - RigolDS1000Z (dummy), nothing verified against hardware yet", ParameterView.FULL)
layout.addWidget(panel)
layout.addWidget(build_fault_buttons(bridge, panel))

# The same controls, same bridge, at the two shorter densities. Click a lamp on any of them to
# change that control's density in place.
layout.addWidget(build_panel(bridge, "COMPACT - the default, and what the category GUIs use", ParameterView.COMPACT))
layout.addWidget(build_panel(bridge, "MINIMAL - for dense per-channel grids", ParameterView.MINIMAL))

# A DS1000E: the timebase controls are genuinely impossible over SCPI on this instrument, so they
# come up dark and disabled rather than raising FeatureUnavailable when clicked.
scope_e = RigolDS1000E("DUMMY", log=log, dummy=True)
bridge_e = OwningBridge(scope_e, poll_interval_s=1.0)
layout.addWidget(build_panel(bridge_e, "RigolDS1000E (dummy) - timebase is unavailable on this hardware", ParameterView.COMPACT))

central = QWidget()
central.setLayout(layout)
window.setCentralWidget(central)

bridge.start()
bridge_e.start()

window.resize(1150, 900)
window.show()
app.exec()

bridge.stop()
bridge_e.stop()
