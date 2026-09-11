""" Loading a saved state from the Instrument menu.

"Load State..." is the common case - put the instrument back the way it was - so it loads, sends
and updates the panel. "Load State to GUI Only..." stages the values in the panel's setpoints
without sending anything, and those staged values must survive the next poll.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

import pylogfile.base as plf

from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from constellation.ui import ConstellationWindow, OwningBridge, SEND_COLORS

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

@pytest.fixture
def saved_state(tmp_path, log):
	''' A state file with channel 1 on a 5 kHz square and channel 2 at 0.25 Vpp. '''

	import constellation.instrument_control.all as everything

	awg = _awg(log)
	awg.set_waveform(1, everything.AWG.WAVE_SQUARE)
	awg.set_frequency(1, 5000.0)
	awg.set_amplitude(2, 0.25)

	path = str(tmp_path / "saved.hdf")
	assert awg.dump_state(path)

	return path

def _panel(log):
	''' An AWG panel on a fresh (1 kHz, 1 Vpp) dummy, bridge not started - requests stay queued. '''

	from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_gui import ArbitraryWaveformGeneratorWidget

	driver = _awg(log)
	window = ConstellationWindow(log)
	bridge = OwningBridge(driver)
	widget = ArbitraryWaveformGeneratorWidget(window, bridge, log)
	widget.panel_title = "AWG"
	bridge.state_changed.emit(driver.state)
	window._rebuild_instrument_menu()

	return window, widget, bridge

def _choose_file(monkeypatch, path):
	monkeypatch.setattr(QFileDialog, "getOpenFileName", staticmethod(lambda *a, **k: (path, "")))

def _no_popups(monkeypatch):
	def fail(*a, **k):
		raise AssertionError("unexpected message box")
	monkeypatch.setattr(QMessageBox, "information", staticmethod(fail))

def _menu(window) -> dict:
	return {a.text(): a for a in window.instrument_menu.actions() if a.text()}

def _drain(bridge) -> list:

	requests = []
	while not bridge._queue.empty():
		requests.append(bridge._queue.get_nowait()[:2])

	return requests

# --- the driver ----------------------------------------------------------------------------------

def test_restore_and_apply_loads_then_applies(saved_state, log, monkeypatch):

	awg = _awg(log)
	applied = []
	original = awg.apply_state
	monkeypatch.setattr(awg, "apply_state", lambda: (applied.append(awg.state.channels[1].frequency), original())[1])

	assert awg.restore_and_apply_state(saved_state) is True

	assert applied == [5000.0]                 # applied, and with the LOADED values
	assert awg.state.channels[1].frequency == 5000.0
	assert awg.state.channels[2].amplitude == 0.25

# --- Load State... (the default: load, send, update) --------------------------------------------

def test_load_state_sends_one_combined_request(qt_app, log, saved_state, monkeypatch):
	""" Not restore_state then apply_state: the bridge refreshes after every command, which would
	replace the loaded values before apply_state ever saw them. """

	window, widget, bridge = _panel(log)
	_choose_file(monkeypatch, saved_state)
	_no_popups(monkeypatch)

	_menu(window)["Load State..."].trigger()

	assert _drain(bridge) == [("restore_and_apply_state", (saved_state,))]

def test_load_state_updates_the_panel_through_the_bridge(qt_app, log, saved_state):
	""" End to end on the bridge's own worker path: after the command, the emitted state is the
	loaded one, so the panel's setpoints follow it. """

	window, widget, bridge = _panel(log)

	bridge._execute("restore_and_apply_state", (saved_state,), {})

	assert widget.channel_controls[1]["frequency"].setpoint == 5000.0
	assert widget.channel_controls[2]["amplitude"].setpoint == 0.25

def test_cancelling_the_file_dialog_does_nothing(qt_app, log, monkeypatch):

	window, widget, bridge = _panel(log)
	_choose_file(monkeypatch, "")

	_menu(window)["Load State..."].trigger()
	_menu(window)["Load State to GUI Only..."].trigger()

	assert _drain(bridge) == []

# --- Load State to GUI Only... -----------------------------------------------------------------

def test_gui_only_fills_the_setpoints_and_sends_nothing(qt_app, log, saved_state, monkeypatch):

	window, widget, bridge = _panel(log)
	_choose_file(monkeypatch, saved_state)

	_menu(window)["Load State to GUI Only..."].trigger()

	freq = widget.channel_controls[1]["frequency"]

	assert freq.setpoint == 5000.0
	assert freq.staged
	assert freq.lamp_send.color == SEND_COLORS["unsent"]
	assert widget.channel_controls[2]["amplitude"].setpoint == 0.25
	assert _drain(bridge) == []

def test_a_staged_value_survives_the_next_poll(qt_app, log, saved_state, monkeypatch):
	""" Following the instrument would otherwise put the instrument's 1 kHz straight back. """

	window, widget, bridge = _panel(log)
	_choose_file(monkeypatch, saved_state)
	_menu(window)["Load State to GUI Only..."].trigger()

	bridge.state_changed.emit(bridge.driver.state)    # the instrument still says 1 kHz

	freq = widget.channel_controls[1]["frequency"]
	assert freq.setpoint == 5000.0
	assert freq.pv_display.text() == "1.0"             # kHz - the instrument's value, on the PV row

def test_sending_a_staged_value_clears_it(qt_app, log, saved_state, monkeypatch):

	window, widget, bridge = _panel(log)
	_choose_file(monkeypatch, saved_state)
	_menu(window)["Load State to GUI Only..."].trigger()

	freq = widget.channel_controls[1]["frequency"]
	freq.resend()

	assert not freq.staged
	assert ("set_frequency", (1, 5000.0)) in _drain(bridge)

def test_a_state_for_another_instrument_loads_nothing(qt_app, log, tmp_path, monkeypatch):
	""" A scope's state has no AWG fields; every control is left as it was, and nothing breaks. """

	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	path = str(tmp_path / "scope.hdf")
	RigolDS1000Z("DUMMY", log=log, dummy=True).dump_state(path)

	window, widget, bridge = _panel(log)

	from constellation.ui import hdf_to_dict, from_serial_dict
	assert widget.stage_setpoints(from_serial_dict(hdf_to_dict(path))) == 0
	assert not any(c.staged for c in widget.parameter_controls())

def test_an_unreadable_file_is_reported_not_raised(qt_app, log, tmp_path, monkeypatch):

	bad = tmp_path / "not-a-state.hdf"
	bad.write_text("nonsense")

	window, widget, bridge = _panel(log)
	_choose_file(monkeypatch, str(bad))

	warnings = []
	monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warnings.append(a)))

	_menu(window)["Load State to GUI Only..."].trigger()

	assert warnings
	assert not any(c.staged for c in widget.parameter_controls())
