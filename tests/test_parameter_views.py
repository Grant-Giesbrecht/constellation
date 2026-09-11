""" Switching many controls between the full and compact views at once: a window-wide default
(the --full / --compact flags), the View menu, and a control's "Apply to similar" button.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import argparse

import pytest

pytest.importorskip("PyQt6")

import pylogfile.base as plf

from PyQt6.QtWidgets import QApplication

from constellation.ui import (ConstellationWindow, OwningBridge, ParameterView, add_view_arguments,
	view_from_arguments)

FULL, COMPACT = ParameterView.FULL, ParameterView.COMPACT

@pytest.fixture(scope="module")
def qt_app():

	yield QApplication.instance() or QApplication([])

@pytest.fixture
def log():

	pile = plf.LogPile()
	pile.terminal_level = plf.CRITICAL

	return pile

@pytest.fixture(autouse=True)
def _close_figures():

	yield

	import matplotlib.pyplot as plt
	plt.close("all")

def _awg_panel(window, log, title="AWG"):
	''' An AWG panel whose bridge is never started. Its first state is delivered through the bridge
	signal, as a real poll would be, so every state_changed slot runs - including the one that
	applies the window's default view to controls built lazily. '''

	import constellation.instrument_control.all as everything
	from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_gui import ArbitraryWaveformGeneratorWidget

	driver = everything.Keysight33500("DUMMY", log=log, dummy=True)
	bridge = OwningBridge(driver)
	widget = ArbitraryWaveformGeneratorWidget(window, bridge, log)
	widget.panel_title = title
	bridge.state_changed.emit(driver.state)

	return widget

def _views(widget) -> set:
	return {control.view for control in widget.parameter_controls()}

# --- CLI flags -----------------------------------------------------------------------------------

@pytest.mark.parametrize("argv,expected", [(["--full"], FULL), (["--compact"], COMPACT), ([], None)])
def test_the_flags_choose_a_view(argv, expected):

	args = add_view_arguments(argparse.ArgumentParser()).parse_args(argv)

	assert view_from_arguments(args) == expected

def test_the_flags_are_mutually_exclusive():

	with pytest.raises(SystemExit):
		add_view_arguments(argparse.ArgumentParser()).parse_args(["--full", "--compact"])

def test_an_unknown_window_view_is_refused(qt_app, log):

	with pytest.raises(ValueError):
		ConstellationWindow(log, add_menu=False, parameter_view="enormous")

# --- window-wide default -------------------------------------------------------------------------

def test_the_window_default_reaches_controls_built_later(qt_app, log):
	""" The AWG builds its channel controls from its first state update, after the window exists. """

	window = ConstellationWindow(log, add_menu=False, parameter_view=FULL)
	widget = _awg_panel(window, log)

	assert widget.channel_controls        # built lazily
	assert _views(widget) == {FULL}

def test_without_a_default_controls_keep_their_own_view(qt_app, log):

	widget = _awg_panel(ConstellationWindow(log, add_menu=False), log)

	assert _views(widget) == {COMPACT}

def test_a_control_switched_by_hand_is_not_switched_back_by_the_next_poll(qt_app, log):

	window = ConstellationWindow(log, add_menu=False, parameter_view=FULL)
	widget = _awg_panel(window, log)

	control = widget.channel_controls[1]["frequency"]
	control.set_view(COMPACT)
	widget.bridge.state_changed.emit(widget.bridge.driver.state)   # another poll

	assert control.view == COMPACT

def test_switching_the_window_switches_every_panel(qt_app, log):

	window = ConstellationWindow(log, add_menu=False)
	first = _awg_panel(window, log, "A")
	second = _awg_panel(window, log, "B")

	window.set_parameter_view(FULL)

	assert _views(first) == _views(second) == {FULL}
	assert window.parameter_view == FULL

def test_switching_one_panel_leaves_the_other_alone(qt_app, log):

	window = ConstellationWindow(log, add_menu=False)
	first = _awg_panel(window, log, "A")
	second = _awg_panel(window, log, "B")

	first.set_parameter_view(FULL)

	assert _views(first) == {FULL}
	assert _views(second) == {COMPACT}

# --- View menu -----------------------------------------------------------------------------------

def _texts(menu) -> list:
	return [a.text() for a in menu.actions() if not a.isSeparator()]

def test_the_view_menu_switches_all_controls(qt_app, log):

	window = ConstellationWindow(log)
	widget = _awg_panel(window, log)
	window._rebuild_instrument_menu()

	actions = {a.text(): a for a in window.view_menu.actions()}
	assert "All Controls: Full" in actions and "All Controls: Compact" in actions

	actions["All Controls: Full"].trigger()
	assert _views(widget) == {FULL}

	actions["All Controls: Compact"].trigger()
	assert _views(widget) == {COMPACT}

def test_with_several_panels_each_gets_its_own_density_options(qt_app, log):

	window = ConstellationWindow(log)
	first = _awg_panel(window, log, "A")
	second = _awg_panel(window, log, "B")
	window._rebuild_instrument_menu()

	submenus = {a.text(): a.menu() for a in window.view_menu.actions() if a.menu() is not None}

	assert set(submenus) == {"A", "B"}
	assert "Controls: Full" in _texts(submenus["A"])

	next(a for a in submenus["A"].actions() if a.text() == "Controls: Full").trigger()

	assert _views(first) == {FULL}
	assert _views(second) == {COMPACT}

def test_the_view_menu_works_before_any_instrument_is_added(qt_app, log):
	""" With nothing docked it still sets the default for what is added next. """

	window = ConstellationWindow(log)

	next(a for a in window.view_menu.actions() if a.text() == "All Controls: Full").trigger()
	widget = _awg_panel(window, log)

	assert _views(widget) == {FULL}

# --- Apply to similar ----------------------------------------------------------------------------

def test_similar_controls_are_the_same_parameter_on_other_channels(qt_app, log):

	widget = _awg_panel(ConstellationWindow(log, add_menu=False), log)
	enable_1 = widget.channel_controls[1]["enable"]

	assert enable_1.similar_controls() == [widget.channel_controls[2]["enable"]]

def test_apply_to_similar_copies_the_display_options(qt_app, log):

	widget = _awg_panel(ConstellationWindow(log, add_menu=False), log)
	freq_1, freq_2 = widget.channel_controls[1]["frequency"], widget.channel_controls[2]["frequency"]

	freq_1.set_view(FULL)
	freq_1.set_lcd(False)
	freq_1.set_follow_instrument(False)

	freq_1.show_details()
	freq_1._dialog.similar_button.click()

	assert (freq_2.view, freq_2.lcd, freq_2.follow_instrument) == (FULL, False, False)
	# ...and nothing that is not the same parameter.
	assert widget.channel_controls[2]["amplitude"].view == COMPACT
	assert widget.channel_controls[1]["amplitude"].view == COMPACT

	freq_1._dialog.close()

def test_apply_to_similar_stays_on_its_own_panel(qt_app, log):
	""" Another instrument's output toggle is not "this channel's output toggle on another channel". """

	window = ConstellationWindow(log, add_menu=False)
	first = _awg_panel(window, log, "A")
	second = _awg_panel(window, log, "B")

	control = first.channel_controls[1]["enable"]
	control.set_view(FULL)
	control.apply_options_to_similar()

	assert first.channel_controls[2]["enable"].view == FULL
	assert second.channel_controls[1]["enable"].view == COMPACT

def test_apply_to_similar_is_disabled_when_there_is_nothing_similar(qt_app):

	from test_parameter_widgets import FakeBridge, FakeState
	from constellation.ui import ParameterBox

	lone = ParameterBox(FakeBridge(), "V/div", get=lambda s: s.value, set_method="set_div_volt")
	lone.show_details()

	assert not lone._dialog.similar_button.isEnabled()

	lone._dialog.close()
