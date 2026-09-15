""" The power supply panel. Runs headless (`QT_QPA_PLATFORM=offscreen`) against a dummy DP832, with
state handed to on_state_changed() directly - no bridge thread, so every test is deterministic. """

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

import pylogfile.base as plf

@pytest.fixture(scope="module")
def qt_app():

	from PyQt6.QtWidgets import QApplication

	yield QApplication.instance() or QApplication([])

@pytest.fixture
def log():

	pile = plf.LogPile()
	pile.terminal_level = plf.CRITICAL

	return pile

def _panel(log):

	from constellation.ui import ConstellationWindow, OwningBridge
	from constellation.instrument_control.power_supply.drivers.Rigol_DP832_dvr import RigolDP832
	from constellation.instrument_control.power_supply.power_supply_gui import PowerSupplyWidget

	driver = RigolDP832("DUMMY", log, dummy=True)
	window = ConstellationWindow(log, add_menu=False)
	widget = PowerSupplyWidget(window, OwningBridge(driver), log)
	widget.on_state_changed(driver.state)

	return widget, driver

def _all_readouts(widget):
	return [r for readouts in widget.readouts.values() for r in readouts.values()]

def test_the_widget_is_registered_for_the_category(qt_app):

	from constellation.ui import _GUI_REGISTRY
	from constellation.instrument_control.power_supply.power_supply_ctg import PowerSupply
	from constellation.instrument_control.power_supply.power_supply_gui import PowerSupplyWidget

	assert _GUI_REGISTRY[PowerSupply] is PowerSupplyWidget

def test_every_channel_gets_every_control_in_its_own_panel(qt_app, log):

	from constellation.ui import CollapsiblePanel

	widget, _ = _panel(log)

	assert sorted(widget.channel_controls) == [1, 2, 3]
	for controls in widget.channel_controls.values():
		assert set(controls) == {"enable", "voltage", "current", "ovp_enable", "ovp_level", "ocp_enable", "ocp_level"}

	assert widget.channels_splitter.count() == 3
	assert all(isinstance(widget.channels_splitter.widget(i), CollapsiblePanel) for i in range(3))

def test_settings_use_parameter_controls_not_the_older_tracked_family(qt_app, log):

	from constellation.ui import _TrackedControlBase, _ParameterControlBase

	widget, _ = _panel(log)

	tracked = widget.findChildren(_TrackedControlBase)
	assert tracked and all(isinstance(c, _ParameterControlBase) for c in tracked)

# --- sections ---------------------------------------------------------------------------------

def test_set_points_and_measured_output_are_framed_sections(qt_app, log):

	widget, _ = _panel(log)

	for ch, controls in widget.channel_controls.items():

		setpoints = widget.setpoint_boxes[ch]
		measured = widget.measured_boxes[ch]

		assert setpoints.title() == "Set Points"
		assert measured.title() == "Measured Output"
		for name in ("enable", "voltage", "current"):
			assert setpoints.isAncestorOf(controls[name])
		for readout in widget.readouts[ch].values():
			assert measured.isAncestorOf(readout)

def test_reading_labels_do_not_repeat_measured(qt_app, log):
	""" The frame's title says it once. """

	from PyQt6.QtWidgets import QLabel

	widget, _ = _panel(log)

	labels = [label.text() for label in widget.measured_boxes[1].findChildren(QLabel)]
	assert "Voltage" in labels
	assert not any(text.lower().startswith("measured") for text in labels)

def test_protection_controls_sit_in_their_own_collapsible_panel(qt_app, log):

	from constellation.ui import CollapsiblePanel

	widget, _ = _panel(log)

	for ch, controls in widget.channel_controls.items():

		protection = widget.protection_panels[ch]

		assert isinstance(protection, CollapsiblePanel)
		for name in ("ovp_enable", "ovp_level", "ocp_enable", "ocp_level"):
			assert protection.isAncestorOf(controls[name])
		assert protection.isAncestorOf(widget.clear_buttons[ch]["ovp"])
		assert not protection.isAncestorOf(controls["voltage"])

def test_each_channels_protection_folds_on_its_own(qt_app, log):

	widget, _ = _panel(log)

	widget.protection_panels[1].set_collapsed(True)

	assert widget.protection_panels[1].collapsed
	assert not widget.protection_panels[2].collapsed

# --- readouts ---------------------------------------------------------------------------------

def test_readings_show_dashes_until_read(qt_app, log):

	widget, _ = _panel(log)

	assert {r.text() for r in _all_readouts(widget)} == {"--"}

def test_readings_are_lcds_by_default(qt_app, log):

	widget, _ = _panel(log)

	for readout in _all_readouts(widget):
		assert readout.is_lcd
		assert readout.lcd.isVisibleTo(readout) and not readout.field.isVisibleTo(readout)

def test_readings_follow_the_state_on_both_displays(qt_app, log):

	from constellation.instrument_control.power_supply.power_supply_gui import format_reading

	widget, driver = _panel(log)
	driver.set_output_enable(2, True)
	driver.refresh_data()
	widget.on_state_changed(driver.state)

	readout = widget.readouts[2]["voltage_meas"]
	expected = format_reading(driver.state.channels[2].voltage_meas)

	assert readout.text() == expected
	assert readout.field.text() == expected
	assert readout.lcd.value() == pytest.approx(float(expected))
	assert readout.field.isReadOnly()

def test_the_view_menu_switches_readings_to_text_fields_and_back(qt_app, log):

	from PyQt6.QtWidgets import QMenu

	widget, _ = _panel(log)

	assert widget.has_view_actions()
	menu = QMenu()
	widget.add_view_actions(menu)
	action = menu.actions()[0]

	assert action.isCheckable() and action.isChecked()

	action.trigger()
	assert not any(r.is_lcd for r in _all_readouts(widget))
	assert all(r.field.isVisibleTo(r) for r in _all_readouts(widget))

	action.trigger()
	assert all(r.is_lcd for r in _all_readouts(widget))

def test_switching_from_code_moves_the_menu_tick(qt_app, log):

	from PyQt6.QtWidgets import QMenu

	widget, _ = _panel(log)
	menu = QMenu()
	widget.add_view_actions(menu)

	widget.set_readings_lcd(False)

	assert not menu.actions()[0].isChecked()

def test_lcd_size_and_colour_can_be_changed(qt_app, log):

	widget, _ = _panel(log)

	widget.set_lcd_appearance(height=48, color="#33ff66", background="#101010")

	for readout in _all_readouts(widget):
		assert readout.lcd.maximumHeight() == 48
		assert "#33ff66" in readout.lcd.styleSheet() and "#101010" in readout.lcd.styleSheet()

@pytest.mark.parametrize("value,expected", [(None, "--"), (4.99812, "4.9981"), (-0.00002, "0.0000"), (-0.25, "-0.2500")])
def test_reading_format(value, expected):

	from constellation.instrument_control.power_supply.power_supply_gui import format_reading

	assert format_reading(value) == expected

# --- protection trips -------------------------------------------------------------------------

def test_clear_is_only_offered_while_tripped(qt_app, log):

	widget, driver = _panel(log)

	assert not widget.clear_buttons[1]["ovp"].isEnabled()

	driver.state.set(["channels", "ovp_tripped"], True, indices=[1])
	widget.on_state_changed(driver.state)

	assert widget.clear_buttons[1]["ovp"].isEnabled()
	assert not widget.clear_buttons[1]["ocp"].isEnabled()
	assert not widget.clear_buttons[2]["ovp"].isEnabled()

def test_clear_asks_the_bridge_for_that_channels_trip(qt_app, log):

	widget, driver = _panel(log)
	driver.state.set(["channels", "ocp_tripped"], True, indices=[3])
	widget.on_state_changed(driver.state)

	calls = []
	widget.bridge.request = lambda *args, **kwargs: calls.append(args)
	widget.clear_buttons[3]["ocp"].click()

	assert calls == [("clear_ocp_trip", 3)]
