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

def test_readings_show_dashes_until_read(qt_app, log):

	widget, _ = _panel(log)

	for readouts in widget.readouts.values():
		assert {r.text() for r in readouts.values()} == {"--"}

def test_readings_follow_the_state(qt_app, log):

	from constellation.instrument_control.power_supply.power_supply_gui import format_reading

	widget, driver = _panel(log)
	driver.set_output_enable(2, True)
	driver.refresh_data()
	widget.on_state_changed(driver.state)

	chan = driver.state.channels[2]
	assert widget.readouts[2]["voltage_meas"].text() == format_reading(chan.voltage_meas)
	assert widget.readouts[2]["power_meas"].text() == format_reading(chan.power_meas)
	assert widget.readouts[2]["voltage_meas"].isReadOnly()

@pytest.mark.parametrize("value,expected", [(None, "--"), (4.99812, "4.9981"), (-0.00002, "0.0000"), (-0.25, "-0.2500")])
def test_reading_format(value, expected):

	from constellation.instrument_control.power_supply.power_supply_gui import format_reading

	assert format_reading(value) == expected

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
