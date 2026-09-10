""" Instrument sync settings: polling on/off and its period, auto-send on/off and its period, and
the status-bar Config button that edits them.

The case that motivated all of it is monitoring - watching an instrument from a panel without the
panel ever writing to it unless the user changes something. These tests pin that down: with
polling off nothing is read on a schedule, with auto-send off nothing is written on a schedule, and
auto-send only ever re-sends values the user actually set.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time

import pytest

pytest.importorskip("PyQt6")

import pylogfile.base as plf

from PyQt6.QtWidgets import QApplication

from constellation.ui import (ConstellationWindow, OwningBridge, InstrumentBridge, InstrumentWidget,
	SyncConfigDialog)

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

def _awg(log):

	import constellation.instrument_control.all as everything

	return everything.Keysight33500("DUMMY", log=log, dummy=True)

class CountingDriver:
	''' Wraps a driver and counts how often the bridge reads the whole instrument. '''

	def __init__(self, driver):
		self._driver = driver
		self.polls = 0

	def poll(self):
		self.polls += 1
		return self._driver.poll()

	def __getattr__(self, name):
		return getattr(self._driver, name)

# --- the bridge ---------------------------------------------------------------------------------

def test_polling_is_on_by_default(log):

	bridge = OwningBridge(_awg(log))

	assert bridge.supports_polling
	assert bridge.poll_enabled

def test_with_polling_off_the_instrument_is_never_read_on_a_schedule(log):

	driver = CountingDriver(_awg(log))
	bridge = OwningBridge(driver, poll_interval_s=0.02, poll_enabled=False)

	bridge.start()
	time.sleep(0.3)
	bridge.stop()

	assert driver.polls == 0

def test_with_polling_on_it_is(log):

	driver = CountingDriver(_awg(log))
	bridge = OwningBridge(driver, poll_interval_s=0.02)

	bridge.start()
	time.sleep(0.3)
	bridge.stop()

	assert driver.polls > 0

def test_a_command_without_polling_does_not_reread_everything(log):
	""" A full refresh after every write is exactly the traffic polling-off exists to avoid. The
	state is still emitted, so the panel still sees the command's own read-back. """

	driver = CountingDriver(_awg(log))
	bridge = OwningBridge(driver, poll_enabled=False)

	emitted = []
	bridge.state_changed.connect(emitted.append)

	bridge._execute("set_frequency", (1, 2500.0), {})

	assert driver.polls == 0
	assert emitted and emitted[-1].channels[1].frequency == 2500.0

def test_a_command_with_polling_does_refresh(log):

	driver = CountingDriver(_awg(log))
	bridge = OwningBridge(driver)

	bridge._execute("set_frequency", (1, 2500.0), {})

	assert driver.polls == 1

def test_set_polling_changes_both_settings(log):

	bridge = OwningBridge(_awg(log))
	bridge.set_polling(False, 0.5)

	assert bridge.poll_enabled is False
	assert bridge.poll_interval_s == 0.5

@pytest.mark.parametrize("bad", [0, -1])
def test_a_nonpositive_poll_period_is_refused(log, bad):

	bridge = OwningBridge(_awg(log))

	with pytest.raises(ValueError):
		bridge.set_polling(True, bad)

	assert bridge.poll_interval_s == 2.0

def test_a_bridge_that_does_not_poll_says_so():

	bridge = InstrumentBridge()

	assert not bridge.supports_polling
	with pytest.raises(NotImplementedError):
		bridge.set_polling(True, 1.0)

# --- auto-send ----------------------------------------------------------------------------------

def _panel(log):
	''' An AWG panel whose bridge is never started, so every request stays in its queue where the
	test can see it. '''

	from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_gui import ArbitraryWaveformGeneratorWidget

	driver = _awg(log)
	window = ConstellationWindow(log, add_menu=False)
	bridge = OwningBridge(driver)
	widget = ArbitraryWaveformGeneratorWidget(window, bridge, log)
	widget.on_state_changed(driver.state)
	bridge.state_changed.emit(driver.state)   # let every control mirror the instrument

	return window, widget, bridge

def _drain(bridge) -> list:

	requests = []
	while not bridge._queue.empty():
		requests.append(bridge._queue.get_nowait()[:2])

	return requests

def test_auto_send_is_off_by_default(qt_app, log):

	_, widget, _ = _panel(log)

	assert widget.auto_send_enabled is False

def test_pushing_resends_only_what_the_user_set(qt_app, log):
	""" A control that has only mirrored the instrument has nothing of the user's to enforce;
	sending it would just echo the instrument back to itself. """

	_, widget, bridge = _panel(log)

	widget.channel_controls[1]["frequency"]._user_changed(5000.0)
	_drain(bridge)

	assert widget.push_setpoints() == 1
	assert _drain(bridge) == [("set_frequency", (1, 5000.0))]

def test_a_panel_nobody_touched_pushes_nothing(qt_app, log):

	_, widget, bridge = _panel(log)

	assert widget.push_setpoints() == 0
	assert _drain(bridge) == []

def test_auto_send_pushes_on_its_timer(qt_app, log):

	from PyQt6.QtTest import QTest

	_, widget, bridge = _panel(log)
	widget.channel_controls[2]["amplitude"]._user_changed(0.5)
	_drain(bridge)

	widget.set_auto_send(True, 0.05)
	QTest.qWait(200)
	widget.set_auto_send(False)

	sent = _drain(bridge)
	assert len(sent) >= 2 and set(sent) == {("set_amplitude", (2, 0.5))}

	QTest.qWait(150)
	assert _drain(bridge) == []   # stopped means stopped

def test_a_nonpositive_auto_send_period_is_refused(qt_app, log):

	_, widget, _ = _panel(log)

	with pytest.raises(ValueError):
		widget.set_auto_send(True, 0)

	assert widget.auto_send_enabled is False

# --- the status bar and the Config window ------------------------------------------------------

@pytest.mark.parametrize("add_menu", [True, False])
def test_every_window_has_a_config_button_in_its_status_bar(qt_app, log, add_menu):

	window = ConstellationWindow(log, add_menu=add_menu)

	assert window.status_bar is window.statusBar()
	assert window.config_button.text() == "Config"

def test_the_config_button_opens_the_sync_window(qt_app, log):

	window, _, _ = _panel(log)

	window.config_button.click()

	assert isinstance(window._sync_dialog, SyncConfigDialog)
	assert window._sync_dialog.isVisible()

	window._sync_dialog.close()

def test_the_sync_window_edits_the_bridge_and_panel_live(qt_app, log):

	window, widget, bridge = _panel(log)
	widget.panel_title = "AWG"

	dialog = SyncConfigDialog(window)
	row = dialog.rows["AWG"]

	assert row["poll_check"].isChecked()
	assert not row["send_check"].isChecked()

	row["poll_period"].setText("0.5")
	row["poll_check"].setChecked(False)
	assert bridge.poll_enabled is False and bridge.poll_interval_s == 0.5

	row["send_period"].setText("3")
	row["send_check"].setChecked(True)
	assert widget.auto_send_enabled and widget.auto_send_interval_s == 3.0

	widget.set_auto_send(False)

def test_an_invalid_period_is_reverted_not_applied(qt_app, log):

	window, widget, bridge = _panel(log)
	widget.panel_title = "AWG"

	row = SyncConfigDialog(window).rows["AWG"]
	row["poll_period"].setText("0")
	row["poll_period"].editingFinished.emit()

	assert bridge.poll_interval_s == 2.0
	assert row["poll_period"].text() == "2"

def test_an_observed_instrument_offers_no_polling(qt_app, log):
	""" Its updates arrive when the owning process broadcasts - there is no schedule here to set. """

	window = ConstellationWindow(log, add_menu=False)
	widget = InstrumentWidget(window, InstrumentBridge(), log)
	widget.panel_title = "Remote"

	row = SyncConfigDialog(window).rows["Remote"]

	assert not row["poll_check"].isEnabled()
	assert not row["poll_period"].isEnabled()
	assert row["send_check"].isEnabled()

def test_the_sync_window_says_when_there_is_nothing_to_configure(qt_app, log):

	from PyQt6.QtWidgets import QLabel

	window = ConstellationWindow(log, add_menu=False)   # held: the dialog dies with its parent
	dialog = SyncConfigDialog(window)

	assert any("No instruments" in label.text() for label in dialog.findChildren(QLabel))
