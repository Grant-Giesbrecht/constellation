""" ConnectionInfoDialog must not flood the instrument with requests.

The bridge emits connection_changed after every command it runs - the dialog's own
connection_summary included. The dialog used to refresh on every emission, so each answer asked
the next question: about a thousand full instrument refreshes a second, carrying on after the
dialog was closed (a closed dialog is only hidden, and stayed connected).

Headless, with a fake bridge that behaves like OwningBridge: every request is followed by a
connection_changed emission.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

@pytest.fixture(scope="module")
def qt_app():

	yield QApplication.instance() or QApplication([])

class EchoingBridge(QObject):

	command_result = pyqtSignal(str, tuple, bool, object)
	connection_changed = pyqtSignal(bool)

	supports_connection_control = True
	auto_reconnect = True
	last_connection_state = None

	def __init__(self):
		super().__init__()
		self.online = True
		self.requests = []

	def describe(self):
		return {"Driver": "Fake"}

	def request(self, method_name, *args, **kwargs):
		self.requests.append(method_name)
		if len(self.requests) > 50:
			raise RuntimeError("request loop")   # fail the test rather than recurse forever
		self.connection_changed.emit(self.online)

def _dialog(bridge):
	from constellation.ui import ConnectionInfoDialog
	return ConnectionInfoDialog(bridge, title="PSU")

def test_opening_does_not_feed_its_own_query_back_into_itself(qt_app):

	bridge = EchoingBridge()
	_dialog(bridge)

	assert bridge.requests.count("connection_summary") <= 2

def test_a_real_change_in_online_status_still_refreshes(qt_app):

	bridge = EchoingBridge()
	_dialog(bridge)
	before = len(bridge.requests)

	bridge.online = False
	bridge.connection_changed.emit(False)

	assert len(bridge.requests) == before + 1

def test_an_unchanged_status_does_not_refresh(qt_app):

	bridge = EchoingBridge()
	_dialog(bridge)
	before = len(bridge.requests)

	for _ in range(5):
		bridge.connection_changed.emit(True)

	assert len(bridge.requests) == before

def test_a_closed_dialog_stops_listening(qt_app):

	bridge = EchoingBridge()
	dialog = _dialog(bridge)

	dialog.reject()
	qt_app.processEvents()
	before = len(bridge.requests)

	bridge.online = False
	bridge.connection_changed.emit(False)
	bridge.command_result.emit("connection_summary", (), True, {"online": False})

	assert len(bridge.requests) == before
