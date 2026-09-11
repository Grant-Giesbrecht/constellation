""" Connection control: noticing a lost instrument quickly, reconnecting when it comes back, the
Connection Info window's Connect / Disconnect / Auto-reconnect / live address, and its large
connection picture with a tooltip on every node and link.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

import pylogfile.base as plf

from PyQt6.QtWidgets import QApplication

from constellation.base import ReconnectPolicy
from constellation.relay import (CommandRelay, DirectSCPIRelay, RelayErrorKind,
	RemoteTextCommandRelayClient, RemoteTextCommandRelayListener, describe_resource)
from constellation.ui import (OwningBridge, ObserverBridge, InstrumentBridge, ConstellationWindow,
	ConnectionIndicator, ConnectionInfoDialog, describe_connection, _BRIDGE_OP)
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

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

def _log():

	pile = plf.LogPile()
	pile.terminal_level = plf.CRITICAL

	return pile

class _Relay(CommandRelay):
	''' A scriptable instrument: each call waits `delay` s, then answers - or fails as a lost
	connection when `alive` is False. probe() follows `alive` too, unless probing is unsupported. '''

	def __init__(self, alive=True, delay=0.0, probe_supported=True):
		super().__init__()
		self.alive = alive
		self.delay = delay
		self.probe_supported = probe_supported
		self.attempts = 0
		self.connects = 0
		self.probes = 0

	def connect(self):
		self.connects += 1
		return self.alive

	def close(self):
		pass

	def _answer(self, reply):
		self.note_success()
		self.attempts += 1
		if self.delay:
			time.sleep(self.delay)
		if not self.alive:
			self.note_failure(ConnectionError("instrument gone"))
			return False, ""
		return True, reply

	def write(self, cmd):
		return self._answer("")[0]

	def read(self):
		return self._answer("1")

	def query(self, cmd):
		return self._answer("FAKE,MODEL-1,SN42,1.0" if "IDN" in cmd else "1")

	def probe(self, timeout_s=1.5):
		self.probes += 1
		if not self.probe_supported:
			return None
		self.note_success()
		if not self.alive:
			self.note_failure(ConnectionError("no answer"))
			return False
		return True

def _scope(relay, policy=None):
	''' A real driver talking to a fake relay: built in dummy mode so construction touches nothing,
	then switched out of it so calls really go through the relay. '''

	scope = RigolDS1000Z("DUMMY", log=_log(), relay=relay, dummy=True, reconnect_policy=policy)
	scope.dummy = False
	scope.online = True

	return scope

# --- not retrying a failure that waited out a timeout -------------------------------------------

def test_a_slow_failure_is_not_retried():
	""" Three timeouts before noticing an unplugged cable is the delay being fixed. """

	relay = _Relay(alive=False, delay=0.05)
	scope = _scope(relay, ReconnectPolicy(num_retries=2, retry_pause_s=0, slow_failure_s=0.02))

	scope.query("*IDN?")

	assert relay.attempts == 1
	assert scope.online is False

def test_a_fast_failure_is_still_retried():
	""" The blip retry exists for is unchanged. """

	relay = _Relay(alive=False)
	scope = _scope(relay, ReconnectPolicy(num_retries=2, retry_pause_s=0, slow_failure_s=0.5))

	scope.query("*IDN?")

	assert relay.attempts == 3

def test_the_slow_failure_rule_can_be_turned_off():

	relay = _Relay(alive=False, delay=0.03)
	scope = _scope(relay, ReconnectPolicy(num_retries=2, retry_pause_s=0, slow_failure_s=None))

	scope.query("*IDN?")

	assert relay.attempts == 3

# --- the liveness check -------------------------------------------------------------------------

def test_ping_says_when_the_instrument_answers():

	scope = _scope(_Relay(alive=True))

	assert scope.ping() is True
	assert scope.online

def test_ping_marks_a_silent_instrument_offline():

	scope = _scope(_Relay(alive=False))

	assert scope.ping() is False
	assert scope.online is False

def test_a_garbled_answer_to_ping_is_not_a_disconnect():

	relay = _Relay(alive=True)

	def garbled(timeout_s=1.5):
		relay.last_error_kind = RelayErrorKind.INSTRUMENT
		return False

	relay.probe = garbled
	scope = _scope(relay)

	assert scope.ping() is True
	assert scope.online

def test_ping_without_a_way_to_probe_is_unknown():

	scope = _scope(_Relay(probe_supported=False))

	assert scope.ping() is None
	assert scope.online

class _FakeVisa:

	def __init__(self, fail=False):
		self.timeout = 30000
		self.fail = fail
		self.seen = []
		self.closed = False

	def query(self, cmd):
		self.seen.append((cmd, self.timeout))
		if self.fail:
			raise TimeoutError("VI_ERROR_TMO")
		return "FAKE"

	def close(self):
		self.closed = True

def _visa_relay(inst=None):

	relay = DirectSCPIRelay()
	relay.configure("TCPIP0::1.2.3.4::INSTR", _log())
	relay.inst = inst

	return relay

def test_the_probe_is_quick_and_restores_the_long_timeout():
	""" The long timeout has to survive: real waveform and trace reads need it. """

	relay = _visa_relay(_FakeVisa())

	assert relay.probe(1.5) is True
	assert relay.inst.seen == [("*IDN?", 1500)]
	assert relay.inst.timeout == 30000

def test_a_failed_probe_restores_the_timeout_and_is_a_connection_failure():

	relay = _visa_relay(_FakeVisa(fail=True))

	assert relay.probe(1.5) is False
	assert relay.inst.timeout == 30000
	assert relay.last_error_kind != RelayErrorKind.INSTRUMENT

def test_reconnecting_closes_the_dead_session_and_bounds_the_open():

	stale = _FakeVisa()
	relay = _visa_relay(stale)
	opened = []
	relay.rm = SimpleNamespace(open_resource=lambda address, open_timeout=None:
		opened.append(open_timeout) or _FakeVisa())

	assert relay.connect()
	assert stale.closed
	assert opened == [relay.open_timeout_ms]
	assert relay.inst.timeout == relay.text_timeout_ms

# --- reading an address -------------------------------------------------------------------------

@pytest.mark.parametrize("address,expected", [
	("GPIB0::5::INSTR", {"Board": "0", "Primary address": "5"}),
	("USB0::0x0957::0x2807::MY57401328::INSTR",
		{"Vendor ID": "0x0957", "Product ID": "0x2807", "Serial number": "MY57401328"}),
	("TCPIP0::192.168.1.91::INSTR", {"Host": "192.168.1.91", "Protocol": "VXI-11"}),
	("TCPIP0::192.168.1.91::5025::SOCKET", {"Host": "192.168.1.91", "Port": "5025", "Protocol": "raw socket"}),
	("TCPIP0::192.168.1.91::hislip0::INSTR", {"Host": "192.168.1.91", "Protocol": "HiSLIP", "Port": "4880"}),
	("ASRL3::INSTR", {"Port": "3"}),
	("ASRL/dev/ttyUSB0::INSTR", {"Port": "/dev/ttyUSB0"}),
	("bench-scope", {"Address": "bench-scope"}),
	("", {}),
])
def test_an_address_is_broken_into_what_a_person_looks_for(address, expected):

	assert describe_resource(address) == expected

# --- networked probing --------------------------------------------------------------------------

def test_the_listener_checks_its_own_instrument():

	listener = RemoteTextCommandRelayListener("GPIB0::5::INSTR", _log(), local_relay=_Relay(alive=False))

	ok, payload = listener.probe(1500)

	assert ok is True and payload["alive"] is False
	assert listener.instrument_online is False

def test_the_listener_reports_its_own_address():

	listener = RemoteTextCommandRelayListener("GPIB0::5::INSTR", _log(), local_relay=_Relay())

	assert listener.status()[1]["address"] == "GPIB0::5::INSTR"

def _client(answers):

	client = RemoteTextCommandRelayClient()
	client.configure("bench", _log())
	client.relay_client = SimpleNamespace(call=lambda name, params: name)

	def run(name, timeout_s=None):
		reply = answers.get(name)
		if isinstance(reply, Exception):
			raise reply
		return reply

	client._run = run

	return client

def test_a_remote_probe_reports_the_bench_side():

	client = _client({"probe": [True, {"alive": False, "instrument_online": False, "last_error_kind": "transport"}]})

	assert client.probe(1.5) is False
	assert client.link_online is True
	assert client.instrument_online is False

def test_an_older_listener_without_probe_is_unknown_not_offline():

	client = _client({"probe": RuntimeError("no such method"), "status": [True, {}]})

	assert client.probe(1.5) is None
	assert client.link_online is True

def test_a_dead_link_is_offline():

	client = _client({"probe": TimeoutError(), "status": TimeoutError()})

	assert client.probe(1.5) is False
	assert client.link_online is False

# --- the driver ---------------------------------------------------------------------------------

def test_set_address_repoints_the_driver_and_its_relay(log):

	import constellation.instrument_control.all as everything

	awg = everything.Keysight33500("DUMMY", log=log, dummy=True)
	awg.set_address("USB0::0x0957::0x5707::MY1::INSTR")

	assert awg.address == awg.id.address == awg.relay.address == "USB0::0x0957::0x5707::MY1::INSTR"
	assert awg.online is False

# --- the bridge ---------------------------------------------------------------------------------

def _run_queued(bridge):
	''' Does what the worker thread would, synchronously, so the test is deterministic. '''

	while not bridge._queue.empty():
		name, args, kwargs = bridge._queue.get_nowait()
		if name == _BRIDGE_OP:
			bridge._run_bridge_op(args[0])
		else:
			bridge._execute(name, args, kwargs)

def test_a_gui_reconnects_by_default():

	scope = _scope(_Relay())
	bridge = OwningBridge(scope)

	assert bridge.auto_reconnect is True
	bridge._apply_reconnect_policy()
	assert scope.reconnect_policy.reconnect_on_use is True

def test_a_replugged_instrument_is_reconnected():

	relay = _Relay(alive=False)
	scope = _scope(relay)
	scope.online = False
	bridge = OwningBridge(scope, poll_enabled=False)
	now = time.time()

	assert bridge._should_reconnect(now)
	bridge._reconnect(now)
	assert scope.online is False                                   # still unplugged

	assert not bridge._should_reconnect(now + 0.5)                 # waits between attempts

	relay.alive = True                                             # plugged back in
	later = now + bridge.reconnect_interval_s
	assert bridge._should_reconnect(later)
	bridge._reconnect(later)

	assert scope.online is True

def test_reconnecting_is_not_attempted_when_not_wanted():

	scope = _scope(_Relay())
	scope.online = False
	bridge = OwningBridge(scope)

	bridge.auto_reconnect = False
	assert not bridge._should_reconnect(time.time())

	bridge.auto_reconnect = True
	bridge._held_offline = True
	assert not bridge._should_reconnect(time.time())

def test_disconnect_keeps_the_instrument_disconnected_until_connect():

	scope = _scope(_Relay())
	bridge = OwningBridge(scope, poll_enabled=False)
	results = []
	bridge.command_result.connect(lambda name, args, ok, result: results.append((name, ok)))

	bridge.disconnect_instrument()
	_run_queued(bridge)

	assert scope.online is False
	assert scope.reconnect_policy.reconnect_on_use is False
	assert not bridge._should_reconnect(time.time() + 100)
	assert ("close", True) in results

	bridge.connect_instrument()
	_run_queued(bridge)

	assert scope.online is True
	assert scope.reconnect_policy.reconnect_on_use is True
	assert ("connect", True) in results

def test_the_address_can_be_changed_live():

	relay = _Relay()
	scope = _scope(relay)
	bridge = OwningBridge(scope, poll_enabled=False)

	bridge.set_address("  GPIB0::9::INSTR ")
	_run_queued(bridge)

	assert scope.address == relay.address == "GPIB0::9::INSTR"
	assert scope.online is True
	assert relay.connects == 1

def test_turning_auto_reconnect_off_reaches_the_driver():

	scope = _scope(_Relay())
	bridge = OwningBridge(scope, poll_enabled=False)

	bridge.set_auto_reconnect(False)
	_run_queued(bridge)

	assert scope.reconnect_policy.reconnect_on_use is False

def test_a_scheduled_poll_pings_first_and_does_not_read_a_lost_instrument():
	""" The fix for the slow icon: a lost instrument is noticed by one quick check, not by every
	getter in refresh_state() waiting out a timeout. """

	scope = _scope(_Relay(alive=False))
	polls = []
	scope.poll = lambda: polls.append(1) or scope.state_to_dict()
	bridge = OwningBridge(scope)
	seen = []
	bridge.connection_state.connect(seen.append)

	bridge._poll_and_emit(refresh=True, ping=True)

	assert polls == []
	assert scope.online is False
	assert seen[-1]["instrument_online"] is False

def test_a_scheduled_poll_of_a_healthy_instrument_reads_it():

	relay = _Relay(alive=True)
	scope = _scope(relay)
	polls = []
	scope.poll = lambda: polls.append(1) or scope.state_to_dict()
	bridge = OwningBridge(scope)

	bridge._poll_and_emit(refresh=True, ping=True)

	assert polls == [1]
	assert relay.probes == 1

def test_commands_are_not_preceded_by_a_ping():
	""" Only scheduled polls check - a command would just pay for an extra round trip. """

	relay = _Relay()
	scope = _scope(relay)
	scope.poll = lambda: scope.state_to_dict()
	bridge = OwningBridge(scope)

	bridge._execute("get_div_time", (), {})

	assert relay.probes == 0

def test_the_bridge_keeps_its_latest_snapshot():

	scope = _scope(_Relay())
	bridge = OwningBridge(scope)

	bridge._poll_and_emit(refresh=False)

	assert bridge.last_connection_state["driver_class"] == "RigolDS1000Z"
	assert bridge.last_connection_state["auto_reconnect"] is True

def test_the_snapshot_names_the_driver_and_address(log):

	import constellation.instrument_control.all as everything

	awg = everything.Keysight33500("TCPIP0::192.168.1.91::INSTR", log=log, dummy=True)
	state = describe_connection(awg)

	assert state["driver_class"] == "Keysight33500"
	assert state["address"] == "TCPIP0::192.168.1.91::INSTR"

# --- the two sizes of picture, and their tooltips -----------------------------------------------

def test_the_status_bar_uses_the_label_free_nodes(qt_app):

	small = ConnectionIndicator(InstrumentBridge(), "AWG")
	big = ConnectionIndicator(InstrumentBridge(), "AWG", labels=True)

	assert small._art_file("instr_online").endswith("instr_online_nl.png")
	assert big._art_file("instr_online").endswith("instr_online.png")
	assert small._art_file("lan_link") == big._art_file("lan_link")     # links have one version

def _network_state(**kwargs):

	state = {"topology": "network", "interface": "lan", "link_online": True, "instrument_online": True,
		"error_kind": "none", "verified": True, "diagnosis": "", "driver_class": "Keysight33500",
		"address": "bench-awg", "relay_id": "bench-awg",
		"idn": "Keysight Technologies,33622A,MY59001234,A.02.03", "expected_idn": "Technologies,33",
		"broker_address": "10.0.0.9", "broker_rpc": "tcp://10.0.0.9:5750",
		"broker_xpub": "tcp://10.0.0.9:5752", "remote_address": "TCPIP0::192.168.1.91::5025::SOCKET"}
	state.update(kwargs)

	return state

def test_each_node_and_link_has_its_own_tooltip(qt_app):

	indicator = ConnectionIndicator(InstrumentBridge(), "AWG", labels=True, detail_tooltips=True)
	indicator.set_connection_state(_network_state())

	tips = dict(zip(indicator.element_roles(), (label.toolTip() for label in indicator._icon_labels)))

	assert set(tips) == {"client", "net", "relay", "bus", "instr"}
	assert "10.0.0.9" in tips["net"]
	assert "bench-awg" in tips["relay"]
	assert "192.168.1.91" in tips["bus"] and "5025" in tips["bus"]
	assert "33622A" in tips["instr"] and "MY59001234" in tips["instr"] and "Keysight33500" in tips["instr"]
	assert indicator.toolTip() == ""       # the per-element tooltips replace the summary

def test_the_status_bar_keeps_one_summary_tooltip(qt_app):

	indicator = ConnectionIndicator(InstrumentBridge(), "AWG")
	indicator.set_connection_state(_network_state())

	assert "AWG" in indicator.toolTip()
	assert all(label.toolTip() == "" for label in indicator._icon_labels)

# --- the Connection Info window -----------------------------------------------------------------

def _dialog():

	bridge = OwningBridge(_scope(_Relay()), poll_enabled=False)
	bridge._poll_and_emit(refresh=False)       # the first report a running bridge would have made

	return bridge, ConnectionInfoDialog(bridge, title="Scope")

def test_the_window_shows_the_large_labeled_picture(qt_app):

	bridge, dialog = _dialog()

	assert dialog.infographic is not None
	assert dialog.infographic.labels and dialog.infographic.detail_tooltips
	assert dialog.infographic.icon_height > 40
	assert dialog.infographic._state is not None    # drawn at once, not blank until the next poll

def test_the_window_controls_act_through_the_bridge(qt_app):

	bridge, dialog = _dialog()
	calls = []
	bridge.connect_instrument = lambda: calls.append("connect")
	bridge.disconnect_instrument = lambda: calls.append("disconnect")
	bridge.set_address = lambda address: calls.append(("address", address))
	bridge.set_auto_reconnect = lambda enabled: calls.append(("auto", enabled))

	dialog.disconnect_button.click()
	dialog.connect_button.click()
	dialog.address_edit.setText("GPIB0::9::INSTR")
	dialog.apply_address_button.click()
	dialog.auto_reconnect_check.setChecked(False)

	assert calls == ["disconnect", "connect", ("address", "GPIB0::9::INSTR"), ("auto", False)]

def test_the_address_field_starts_at_the_current_address(qt_app):

	bridge, dialog = _dialog()

	assert dialog.address_edit.text() == "DUMMY"
	assert dialog.auto_reconnect_check.isChecked()

def test_an_observed_instrument_cannot_be_controlled_from_here(qt_app):

	dialog = ConnectionInfoDialog(ObserverBridge("bench-scope"), title="Remote")

	assert not dialog.connect_button.isEnabled()
	assert not dialog.address_edit.isEnabled()
	assert not dialog.auto_reconnect_check.isEnabled()

# --- remembering auto-reconnect -----------------------------------------------------------------

def _stop(window):

	for bridge in window._bridges:
		bridge.stop()

def test_auto_reconnect_is_remembered(qt_app, log):

	import constellation.instrument_control.all as everything

	first = ConstellationWindow(log, add_menu=False)
	widget = first.add_instrument(driver=everything.Keysight33500("DUMMY", log=log, dummy=True), title="AWG")
	widget.bridge.auto_reconnect = False
	first.save_sync_settings(widget)
	_stop(first)

	second = ConstellationWindow(log, add_menu=False)
	again = second.add_instrument(driver=everything.Keysight33500("DUMMY", log=log, dummy=True), title="AWG")

	assert again.bridge.auto_reconnect is False

	_stop(second)

def test_toggling_auto_reconnect_in_the_window_saves_it(qt_app, log, monkeypatch):

	import constellation.instrument_control.all as everything

	window = ConstellationWindow(log, add_menu=False)
	widget = window.add_instrument(driver=everything.Keysight33500("DUMMY", log=log, dummy=True), title="AWG")
	saved = []
	monkeypatch.setattr(window, "save_sync_settings", lambda w: saved.append(w))

	window._show_connection_info(widget.bridge, "AWG")
	dialog = window._info_dialogs[-1]
	dialog.auto_reconnect_check.setChecked(not dialog.auto_reconnect_check.isChecked())

	assert saved == [widget]

	dialog.close()
	_stop(window)

# --- a short timeout normally, a long one for slow transfers ------------------------------------

def test_ordinary_commands_use_the_short_timeout_and_long_ones_the_long_timeout():
	""" The 30 s timeout exists for a full-memory waveform read. Applying it to every command is
	what made a lost instrument take so long to notice. """

	inst = _FakeVisa()
	relay = DirectSCPIRelay(timeout_ms=30000, text_timeout_ms=3000)
	relay.configure("TCPIP0::1.2.3.4::INSTR", _log())
	relay.rm = SimpleNamespace(open_resource=lambda address, open_timeout=None: inst)

	assert relay.connect()
	assert inst.timeout == 3000

	with relay.long_operation():
		assert inst.timeout == 30000

	assert inst.timeout == 3000

def test_the_long_window_is_restored_even_when_the_transfer_fails():

	inst = _FakeVisa()
	relay = DirectSCPIRelay(text_timeout_ms=3000)
	relay.configure("TCPIP0::1.2.3.4::INSTR", _log())
	relay.inst = inst
	inst.timeout = 3000

	with pytest.raises(RuntimeError):
		with relay.long_operation():
			raise RuntimeError("transfer died")

	assert inst.timeout == 3000

def test_a_driver_can_ask_for_the_long_window():
	''' Driver.long_operation() is what a driver wraps a slow text query in - an ASCII waveform
	read, a DMM reading at a high NPLC. '''

	inst = _FakeVisa()
	relay = DirectSCPIRelay(timeout_ms=30000, text_timeout_ms=3000)
	relay.inst = inst
	inst.timeout = 3000
	scope = _scope(relay)

	with scope.long_operation():
		assert inst.timeout == 30000

	assert inst.timeout == 3000

def test_a_relay_with_no_timeout_to_raise_still_works():

	scope = _scope(_Relay())

	with scope.long_operation():
		pass

# --- the panel is drawn before the instrument answers -------------------------------------------

def test_a_state_is_emitted_as_soon_as_the_bridge_starts():
	""" A panel builds from the first state it gets. Without this it stays empty until the first
	poll - and forever, for an instrument that never answers. """

	scope = _scope(_Relay(alive=False))
	scope.online = False
	bridge = OwningBridge(scope, poll_enabled=False)
	seen = []
	bridge.state_changed.connect(seen.append)

	bridge.start()
	deadline = time.time() + 2
	while not seen and time.time() < deadline:
		QApplication.processEvents()
		time.sleep(0.01)
	bridge.stop()

	assert seen, "no state was emitted before the first poll"
