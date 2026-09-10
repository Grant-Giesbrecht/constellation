""" The arbitrary waveform generator panel, and the waveform estimate it draws.

The estimate is tested without Qt: `estimate_waveform()` is a plain function, and what it claims
about a channel's output is the part a person might act on. The widget tests run headless
(`QT_QPA_PLATFORM=offscreen`) against dummy drivers.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import numpy as np
import pytest

import pylogfile.base as plf

from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_ctg import ArbitraryWaveformGenerator as AWG
from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_gui import (
	estimate_waveform, estimate_time_axis, ESTIMATE_DISCLAIMER, LAYOUT_SIDE_BY_SIDE, LAYOUT_TABS)

def _chan(**overrides):
	''' A stand-in channel state: 1 kHz, 2 Vpp, 0.5 V offset sine, output on. '''

	values = dict(waveform_type=AWG.WAVE_SINE, frequency=1e3, amplitude=2.0, offset=0.5,
		phase=0.0, duty_cycle=50.0, output_enable=True, output_polarity=AWG.POLARITY_NORMAL)
	values.update(overrides)

	return SimpleNamespace(**values)

T = np.linspace(0.0, 3e-3, 30001)

# --- the estimate ----------------------------------------------------------------------------

def test_a_sine_swings_half_the_amplitude_either_side_of_the_offset():
	""" Amplitude is peak-to-peak, so the peaks are offset +/- amplitude/2 - not offset +/- amplitude. """

	y, note = estimate_waveform(_chan(), T)

	assert note is None
	assert y.max() == pytest.approx(1.5, abs=1e-3)
	assert y.min() == pytest.approx(-0.5, abs=1e-3)

def test_phase_shifts_the_waveform():

	y0, _ = estimate_waveform(_chan(phase=0.0, offset=0.0), np.array([0.0]))
	y90, _ = estimate_waveform(_chan(phase=90.0, offset=0.0), np.array([0.0]))

	assert y0[0] == pytest.approx(0.0, abs=1e-9)
	assert y90[0] == pytest.approx(1.0, abs=1e-9)

def test_a_square_spends_its_duty_cycle_high():

	y, _ = estimate_waveform(_chan(waveform_type=AWG.WAVE_SQUARE, duty_cycle=25.0), T)

	assert np.mean(y > 0.5) == pytest.approx(0.25, abs=0.01)
	assert set(np.round(np.unique(y), 6)) == {-0.5, 1.5}

def test_inverting_flips_about_the_offset_not_about_zero():
	""" The same rule the hardware suite's polarity prompt checks on the instrument. """

	normal, _ = estimate_waveform(_chan(), T)
	inverted, _ = estimate_waveform(_chan(output_polarity=AWG.POLARITY_INVERTED), T)

	assert np.allclose(inverted - 0.5, -(normal - 0.5))
	assert inverted.mean() == pytest.approx(0.5, abs=1e-3)

def test_dc_is_just_the_offset():

	y, note = estimate_waveform(_chan(waveform_type=AWG.WAVE_DC, amplitude=None, frequency=None), T)

	assert note is None
	assert np.all(y == 0.5)

def test_an_arbitrary_waveform_is_not_guessed_at():
	""" Its contents are not tracked. Drawing anything would be inventing an output. """

	y, note = estimate_waveform(_chan(waveform_type=AWG.WAVE_ARB), T)

	assert y is None
	assert "arbitrary" in note

@pytest.mark.parametrize("missing", ["waveform_type", "frequency", "amplitude", "offset"])
def test_a_missing_setting_draws_nothing_rather_than_a_default(missing):
	""" A value that has never been read is unknown, not zero. """

	y, note = estimate_waveform(_chan(**{missing: None}), T)

	assert y is None
	assert note

def test_the_simplifications_are_admitted():

	for wave in (AWG.WAVE_RAMP, AWG.WAVE_NOISE):
		y, note = estimate_waveform(_chan(waveform_type=wave), T)
		assert y is not None and note, wave

def test_noise_is_stable_between_redraws():
	""" A reseeded sample on every poll would crawl across the plot and read as a live signal. """

	a, _ = estimate_waveform(_chan(waveform_type=AWG.WAVE_NOISE), T)
	b, _ = estimate_waveform(_chan(waveform_type=AWG.WAVE_NOISE), T)

	assert np.array_equal(a, b)

def test_the_time_axis_shows_several_cycles_of_the_slowest_channel():

	t = estimate_time_axis([_chan(frequency=1e3), _chan(frequency=1e5)])

	assert t[-1] == pytest.approx(3e-3)
	# ...with enough points that the fast channel is not aliased: 300 cycles x 200 points.
	assert len(t) >= 50000 or len(t) >= 300 * 200 * 0.99

def test_the_time_axis_survives_nothing_periodic():

	t = estimate_time_axis([_chan(waveform_type=AWG.WAVE_DC)])

	assert len(t) > 1 and t[-1] > 0

# --- the widget ------------------------------------------------------------------------------

pytest.importorskip("PyQt6")

@pytest.fixture(scope="module")
def qt_app():

	from PyQt6.QtWidgets import QApplication

	yield QApplication.instance() or QApplication([])

@pytest.fixture
def log():

	pile = plf.LogPile()
	pile.terminal_level = plf.CRITICAL

	return pile

@pytest.fixture(autouse=True)
def _close_figures():
	''' Every panel builds a PlotWidget, which opens a pyplot figure that pyplot keeps alive until
	it is explicitly closed. Left open, a dozen of these push the session past matplotlib's
	20-figure warning, which then surfaces in some unrelated later test. '''

	yield

	import matplotlib.pyplot as plt
	plt.close("all")

def _panel(log, driver_name="Keysight33500"):
	''' A panel over a dummy generator with channel 2 on a 25% square, output on. The state is
	handed to on_state_changed() directly - no bridge thread, so the test is deterministic. '''

	from constellation.ui import ConstellationWindow, OwningBridge
	from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_gui import ArbitraryWaveformGeneratorWidget
	import constellation.instrument_control.all as everything

	driver = getattr(everything, driver_name)("DUMMY", log=log, dummy=True)
	driver.set_waveform(2, AWG.WAVE_SQUARE)
	driver.set_duty_cycle(2, 25.0)
	driver.set_output_enable(2, True)

	window = ConstellationWindow(log, add_menu=False)
	widget = ArbitraryWaveformGeneratorWidget(window, OwningBridge(driver), log)
	widget.on_state_changed(driver.state)

	return widget, driver

def test_the_widget_is_registered_for_the_category(qt_app):

	from constellation.ui import _GUI_REGISTRY
	from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_gui import ArbitraryWaveformGeneratorWidget

	assert _GUI_REGISTRY[AWG] is ArbitraryWaveformGeneratorWidget

@pytest.mark.parametrize("driver_name", ["Keysight33500", "SiglentSDG2000X"])
def test_every_channel_gets_every_control(qt_app, log, driver_name):

	widget, _ = _panel(log, driver_name)

	assert sorted(widget.channel_controls) == [1, 2]
	for controls in widget.channel_controls.values():
		assert set(controls) == {"enable", "waveform", "frequency", "amplitude", "offset", "phase",
			"duty", "load", "polarity"}

def test_the_plot_is_to_the_left_of_the_channels(qt_app, log):

	widget, _ = _panel(log)

	assert widget.body_splitter.widget(0) is widget.waveform_box
	assert widget.body_splitter.widget(1) is widget.channels_box

def test_the_disclaimer_is_shown_and_says_estimate(qt_app, log):

	widget, _ = _panel(log)

	assert widget.disclaimer.text() == ESTIMATE_DISCLAIMER
	assert "estimate" in ESTIMATE_DISCLAIMER.lower() and "not" in ESTIMATE_DISCLAIMER.lower()
	assert widget.disclaimer.isVisibleTo(widget.waveform_box)

def test_the_disclaimer_is_one_short_line_with_detail_in_the_tooltip(qt_app, log):
	""" It should not out-shout the controls: one line on the panel, the caveats on hover. """

	widget, _ = _panel(log)

	assert len(ESTIMATE_DISCLAIMER) < 60
	assert not widget.disclaimer.wordWrap()
	assert "not measured" in widget.disclaimer.toolTip() or "not guaranteed" in widget.disclaimer.toolTip()

def test_the_figure_itself_is_marked_as_an_estimate(qt_app, log):
	""" A screenshot or saved image of the plot alone must still say what it is. """

	widget, _ = _panel(log)

	texts = [t.get_text().lower() for t in widget.plot_widget.ax1a.texts]

	assert any("estimate" in t for t in texts)

def test_a_disabled_output_is_drawn_but_labelled(qt_app, log):

	widget, _ = _panel(log)

	lines = {line.get_label(): line for line in widget.plot_widget.ax1a.get_lines()}

	assert set(lines) == {"Ch1 (output off)", "Ch2"}
	assert lines["Ch1 (output off)"].get_linestyle() == "--"

def test_switching_to_tabs_and_back_moves_the_same_controls(qt_app, log):
	""" A reparent, not a rebuild - rebuilt controls would lose their setpoints. """

	widget, _ = _panel(log)
	before = {ch: dict(c) for ch, c in widget.channel_controls.items()}

	widget.set_channel_layout(LAYOUT_TABS)

	assert widget.channels_stack.currentWidget() is widget.channels_tabs
	assert widget.channels_tabs.count() == 2

	widget.set_channel_layout(LAYOUT_SIDE_BY_SIDE)

	assert widget.channels_stack.currentWidget() is widget.channels_splitter
	assert widget.channels_tabs.count() == 0
	for ch, body in widget._channel_bodies.items():
		assert body.parent() is widget._channel_panels[ch].content

	assert widget.channel_controls == before

def test_an_unknown_layout_is_refused(qt_app, log):

	widget, _ = _panel(log)

	with pytest.raises(ValueError):
		widget.set_channel_layout("diagonal")

def test_duty_cycle_is_greyed_out_where_it_does_not_apply(qt_app, log):
	""" Sending a duty cycle to a sine is an instrument error, so the control shouldn't offer it. """

	widget, driver = _panel(log)

	assert not widget.channel_controls[1]["duty"].isEnabled()   # sine
	assert widget.channel_controls[2]["duty"].isEnabled()       # square

	driver.set_waveform(1, AWG.WAVE_PULSE)
	widget.on_state_changed(driver.state)

	assert widget.channel_controls[1]["duty"].isEnabled()

def test_an_unchanged_state_does_not_redraw(qt_app, log):
	""" The bridge polls every couple of seconds; redrawing an identical figure each time is waste. """

	widget, driver = _panel(log)

	calls = []
	widget._redraw_estimate = lambda channels: calls.append(channels)

	widget.on_state_changed(driver.state)
	assert calls == []

	driver.set_frequency(1, 2e3)
	widget.on_state_changed(driver.state)
	assert len(calls) == 1

# --- the View menu -------------------------------------------------------------------------------

def test_the_layout_choice_is_not_a_control_on_the_panel(qt_app, log):
	""" Display preferences live in the View menu, not among the instrument's controls. """

	from PyQt6.QtWidgets import QComboBox

	widget, _ = _panel(log)

	combos = [c for c in widget.channels_box.findChildren(QComboBox)
		if not any(c.parent() is ctl or ctl.isAncestorOf(c)
			for controls in widget.channel_controls.values() for ctl in controls.values())]

	assert combos == []
	assert not hasattr(widget, "layout_selector")

def _window_with_panel(log):

	from constellation.ui import ConstellationWindow
	import constellation.instrument_control.all as everything

	window = ConstellationWindow(log)   # with the menu bar
	driver = everything.Keysight33500("DUMMY", log=log, dummy=True)
	widget = window.add_instrument(driver=driver, title="AWG")
	widget.on_state_changed(driver.state)

	return window, widget

def _view_actions(window):

	return {a.text(): a for a in window.view_menu.actions() if not a.isSeparator()}

def test_the_view_menu_offers_both_layouts(qt_app, log):

	window, widget = _window_with_panel(log)
	actions = _view_actions(window)

	assert set(actions) == {"Channels Side by Side", "Channels in Tabs"}
	assert actions["Channels Side by Side"].isChecked()
	assert not actions["Channels in Tabs"].isChecked()

	for bridge in window._bridges:
		bridge.stop()

def test_choosing_from_the_view_menu_switches_the_layout(qt_app, log):

	window, widget = _window_with_panel(log)
	actions = _view_actions(window)

	actions["Channels in Tabs"].trigger()

	assert widget.channel_layout == LAYOUT_TABS
	assert widget.channels_stack.currentWidget() is widget.channels_tabs
	assert actions["Channels in Tabs"].isChecked()
	assert not actions["Channels Side by Side"].isChecked()

	for bridge in window._bridges:
		bridge.stop()

def test_a_layout_change_from_code_moves_the_menu_tick(qt_app, log):

	window, widget = _window_with_panel(log)

	widget.set_channel_layout(LAYOUT_TABS)

	assert _view_actions(window)["Channels in Tabs"].isChecked()

	for bridge in window._bridges:
		bridge.stop()

def test_panels_without_view_options_leave_an_honest_empty_menu(qt_app, log):

	from constellation.ui import ConstellationWindow

	window = ConstellationWindow(log)
	actions = window.view_menu.actions()

	assert len(actions) == 1 and not actions[0].isEnabled()
