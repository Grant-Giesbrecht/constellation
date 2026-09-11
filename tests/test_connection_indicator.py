""" The status-bar connection indicator: which bus an instrument is on, how the bridge reports the
connection, and which icons the chain is drawn with for each state.

The icon choice is tested through ConnectionIndicator.icon_plan(), which is the whole decision -
drawing is just loading the files it names. One test checks every file any plan can name exists.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import itertools
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("PyQt6")

import pylogfile.base as plf

from PyQt6.QtWidgets import QApplication

from constellation.relay import (interface_from_resource, DirectSCPIRelay, VICPDirectSCPIRelay,
	RemoteTextCommandRelayClient, RemoteTextCommandRelayListener, RelayErrorKind)
from constellation.ui import (ConnectionIndicator, InstrumentBridge, OwningBridge, ConstellationWindow,
	describe_connection, ASSETS_DIR)

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

# --- which bus ----------------------------------------------------------------------------------

@pytest.mark.parametrize("address,expected", [
	("GPIB0::5::INSTR", "gpib"),
	("USB0::0x0957::0x2807::MY57401328::INSTR", "usb"),
	("TCPIP0::192.168.1.91::INSTR", "lan"),
	("TCPIP0::192.168.1.91::5025::SOCKET", "lan"),
	("tcpip0::192.168.1.91::inst0::INSTR", "lan"),
	("ASRL3::INSTR", "serial"),
	("VICP::192.168.1.20", "lan"),
	("", "other"),
	(None, "other"),
	("my-labmesh-relay", "other"),
])
def test_the_bus_is_read_from_the_resource_prefix(address, expected):

	assert interface_from_resource(address) == expected

def test_a_local_relay_reports_its_bus():

	relay = DirectSCPIRelay()
	relay.address = "GPIB0::5::INSTR"

	assert relay.interface() == "gpib"

def test_vicp_is_always_lan():
	""" Its address is a bare host name, with no VISA prefix to read. """

	relay = VICPDirectSCPIRelay()
	relay.address = "192.168.1.20"

	assert relay.interface() == "lan"

def test_the_listener_reports_its_bus(log):
	""" A networked client's address is a labmesh relay id - only the bench side knows the bus. """

	listener = RemoteTextCommandRelayListener("USB0::0x0957::0x2807::MY1::INSTR", log)

	ok, payload = listener.status()

	assert ok and payload["interface"] == "usb"

def test_the_client_learns_the_bus_when_it_connects(log, monkeypatch):

	client = RemoteTextCommandRelayClient()
	client.configure("bench-awg", log)
	client.relay_client = SimpleNamespace(call=lambda *a, **k: None)
	monkeypatch.setattr(client, "_run", lambda coro, timeout_s=None: [True,
		{"instrument_online": True, "interface": "gpib", "last_error_kind": "none"}])

	assert client.interface() is None     # unknown until asked
	client._probe_status()

	assert client.interface() == "gpib"
	assert client.instrument_online is True

def test_a_failed_probe_leaves_the_bus_unknown_rather_than_failing(log, monkeypatch):

	client = RemoteTextCommandRelayClient()
	client.configure("bench-awg", log)
	client.relay_client = SimpleNamespace(call=lambda *a, **k: None)

	def boom(*a, **k):
		raise TimeoutError("no answer")
	monkeypatch.setattr(client, "_run", boom)

	client._probe_status()

	assert client.interface() is None

# --- what the bridge reports --------------------------------------------------------------------

def _driver(online=True, relay=None, dummy=False):

	return SimpleNamespace(dummy=dummy, online=online, relay=relay, verified_hardware=True,
		connection_summary=lambda: {"diagnosis": "Connected." if online else "Offline."})

def test_a_dummy_is_described_as_simulated():

	assert describe_connection(_driver(dummy=True))["topology"] == "dummy"

def test_a_local_instrument_is_described_by_its_bus_and_the_drivers_view():

	relay = DirectSCPIRelay()
	relay.address = "TCPIP0::192.168.1.91::INSTR"

	state = describe_connection(_driver(online=False, relay=relay))

	assert state["topology"] == "local"
	assert state["interface"] == "lan"
	assert state["instrument_online"] is False

def test_a_networked_instrument_reports_both_links(log):

	relay = RemoteTextCommandRelayClient()
	relay.configure("bench-awg", log)
	relay.link_online = True
	relay.instrument_online = False
	relay.remote_interface = "usb"
	relay.last_error_kind = RelayErrorKind.TRANSPORT

	state = describe_connection(_driver(online=False, relay=relay))

	assert state["topology"] == "network"
	assert (state["link_online"], state["instrument_online"], state["interface"]) == (True, False, "usb")
	assert state["error_kind"] == "transport"

def test_the_bridge_reports_after_every_poll(qt_app, log):

	import constellation.instrument_control.all as everything

	bridge = OwningBridge(everything.Keysight33500("DUMMY", log=log, dummy=True))
	seen = []
	bridge.connection_state.connect(seen.append)

	bridge._execute("set_frequency", (1, 2000.0), {})

	assert seen and seen[-1]["topology"] == "dummy"

# --- which icons --------------------------------------------------------------------------------

def _indicator(state=None, age_s=0.0, poll_enabled=False, poll_interval_s=None):

	bridge = InstrumentBridge()
	bridge.poll_enabled = poll_enabled
	bridge.poll_interval_s = poll_interval_s

	indicator = ConnectionIndicator(bridge, "AWG")
	if state is not None:
		indicator.set_connection_state(state)
		indicator._received = time.time() - age_s
		indicator._refresh()   # what the widget's own 1 s timer would do next

	return indicator

def _state(**kwargs):

	base = {"topology": "local", "interface": "lan", "link_online": None, "instrument_online": True,
		"error_kind": "none", "verified": True, "diagnosis": ""}
	base.update(kwargs)

	return base

def test_local_and_online(qt_app):

	assert _indicator(_state()).icon_plan() == [
		("client_online", False), ("lan_link", False), ("instr_online", False)]

def test_local_and_offline(qt_app):

	assert _indicator(_state(interface="usb", instrument_online=False)).icon_plan() == [
		("client_online", False), ("usb_break", False), ("instr_offline", False)]

def test_a_bus_without_its_own_artwork_uses_other(qt_app):

	plan = _indicator(_state(interface="serial")).icon_plan()

	assert ("other_link", False) in plan

def test_networked_and_all_well(qt_app):

	assert _indicator(_state(topology="network", link_online=True, interface="gpib")).icon_plan() == [
		("client_online", False), ("net_link", False), ("relay_online", False),
		("gpib_link", False), ("instr_online", False)]

def test_networked_with_the_instrument_down(qt_app):

	plan = _indicator(_state(topology="network", link_online=True, instrument_online=False)).icon_plan()

	assert plan[1:] == [("net_link", False), ("relay_online", False), ("lan_break", False),
		("instr_offline", False)]

def test_a_broken_network_link_hides_the_bench_side(qt_app):
	""" Past a broken link the instrument cannot be seen - its last-known state is shown faded. """

	plan = _indicator(_state(topology="network", link_online=False, instrument_online=True)).icon_plan()

	assert plan[1:3] == [("net_break", False), ("relay_offline", False)]
	assert all(faded for _name, faded in plan[3:])

def test_nothing_heard_yet_is_faded(qt_app):

	plan = _indicator().icon_plan()

	assert plan[0] == ("client_online", False)
	assert all(faded for _name, faded in plan[1:])

def test_a_dummy_is_faded(qt_app):

	plan = _indicator(_state(topology="dummy")).icon_plan()

	assert all(faded for _name, faded in plan[1:])

def test_an_old_report_is_faded(qt_app):
	""" Relay flags only change when a call runs - with polling off, green would otherwise mean
	"nothing has been tried lately". """

	plan = _indicator(_state(), age_s=60).icon_plan()

	assert plan == [("client_online", False), ("lan_link", True), ("instr_online", True)]
	assert "Not checked" in _indicator(_state(), age_s=60).toolTip()

def test_a_slow_poll_does_not_fade_between_polls(qt_app):

	indicator = _indicator(_state(), age_s=15, poll_enabled=True, poll_interval_s=10)

	assert indicator.stale_window_s() == 25
	assert not any(faded for _name, faded in indicator.icon_plan())

def test_an_observed_instrument_shows_the_link_it_can_see(qt_app):

	plan = _indicator(_state(topology="observer", link_online=True, instrument_online=None,
		interface=None)).icon_plan()

	assert plan[1:3] == [("net_link", False), ("relay_online", False)]
	assert plan[3:] == [("other_link", True), ("instr_online", True)]

def test_every_icon_any_state_can_name_exists(qt_app):

	names = set()
	for topology, interface, link, instrument, age in itertools.product(
			("local", "network", "observer", "dummy"), ("lan", "usb", "gpib", "serial", None),
			(True, False, None), (True, False, None), (0, 60)):
		plan = _indicator(_state(topology=topology, interface=interface, link_online=link,
			instrument_online=instrument), age_s=age).icon_plan()
		names.update(name for name, _faded in plan)

	missing = [n for n in sorted(names) if not os.path.exists(os.path.join(ASSETS_DIR, f"{n}.png"))]

	assert missing == []

def test_all_icons_share_one_scale(qt_app):
	""" A link must keep the thickness it was drawn with relative to a node. """

	from PyQt6.QtGui import QPixmap

	indicator = _indicator(_state())
	node = indicator._pixmap("instr_online", False)
	link = indicator._pixmap("lan_link", False)

	node_art = QPixmap(os.path.join(ASSETS_DIR, "instr_online.png"))
	link_art = QPixmap(os.path.join(ASSETS_DIR, "lan_link.png"))

	drawn = link.height() / node.height()
	designed = link_art.height() / node_art.height()

	assert abs(drawn - designed) < 0.05

# --- the window ---------------------------------------------------------------------------------

def test_each_instrument_gets_an_indicator_that_opens_its_details(qt_app, log, monkeypatch):

	import constellation.instrument_control.all as everything

	window = ConstellationWindow(log, add_menu=False)
	opened = []
	monkeypatch.setattr(window, "_show_connection_info", lambda bridge, title: opened.append(title))

	first = window.add_instrument(driver=everything.Keysight33500("DUMMY", log=log, dummy=True), title="AWG")
	window.add_instrument(driver=everything.SiglentSDG2000X("DUMMY", log=log, dummy=True), title="SDG")

	assert [i.title for i in window.connection_indicators] == ["AWG", "SDG"]

	first.connection_indicator.clicked.emit()
	assert opened == ["AWG"]

	for bridge in window._bridges:
		bridge.stop()
