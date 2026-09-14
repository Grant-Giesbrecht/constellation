""" The older `Tracked*` controls (`ui.py`). Still used by the data-acquisition GUI, so its two known
bugs were fixed rather than the family retired: a background poll overwrote half-typed text, and
the mismatch check used exact equality.

Headless (`QT_QPA_PLATFORM=offscreen`), with a fake bridge in place of an instrument.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

from constellation.ui import TrackedValue

@pytest.fixture(scope="module")
def qt_app():

	yield QApplication.instance() or QApplication([])

class FakeBridge(QObject):
	''' Records requests instead of talking to anything; emits the same signals a real bridge does. '''

	state_changed = pyqtSignal(object)
	command_result = pyqtSignal(str, tuple, bool, object)
	connection_changed = pyqtSignal(bool)

	def __init__(self):
		super().__init__()
		self.requests = []

	def request(self, method_name, *args, **kwargs):
		self.requests.append((method_name, args))

def _value(bridge, **kwargs):
	return TrackedValue(bridge, "Volts", get=lambda s: s["v"], set_method="set_v", **kwargs)

def _type(control, text):
	''' What a user typing does: setText() alone doesn't emit textEdited, which is the signal the
	control watches. '''
	control.edit.setText(text)
	control.edit.textEdited.emit(text)

def test_a_poll_does_not_overwrite_half_typed_text(qt_app):
	""" The guard used to be hasFocus(), which is False whenever the window isn't active - so a
	background poll replaced what the user was typing. """

	bridge = FakeBridge()
	control = _value(bridge)
	bridge.state_changed.emit({"v": 1.0})

	_type(control, "0.002")
	bridge.state_changed.emit({"v": 1.0})

	assert control.edit.text() == "0.002"

def test_committing_releases_the_overwrite_guard(qt_app):
	""" The guard holds only while text is uncommitted - after that the control shows its setpoint
	again, including when the committed text wasn't a number. """

	bridge = FakeBridge()
	control = _value(bridge)
	bridge.state_changed.emit({"v": 1.0})

	_type(control, "not a number")
	control.edit.editingFinished.emit()

	assert control._dirty is False
	assert control.edit.text() == "1.0"

def test_committing_an_untouched_field_sends_nothing(qt_app):
	""" editingFinished fires on focus-out whether or not anything changed. """

	bridge = FakeBridge()
	control = _value(bridge)
	bridge.state_changed.emit({"v": 1.0})

	control.edit.editingFinished.emit()

	assert bridge.requests == []

def test_committing_typed_text_sends_it(qt_app):

	bridge = FakeBridge()
	control = _value(bridge)

	_type(control, "0.002")
	control.edit.editingFinished.emit()

	assert bridge.requests == [("set_v", (0.002,))]

def test_a_quantized_read_back_is_not_a_mismatch(qt_app):
	""" An instrument that stores 1.004 when asked for 1.0 is working correctly. """

	bridge = FakeBridge()
	control = _value(bridge)

	control._user_changed(1.0)
	bridge.state_changed.emit({"v": 1.004})

	assert control._status() == "confirmed"

def test_a_genuinely_different_read_back_is_still_a_mismatch(qt_app):

	bridge = FakeBridge()
	control = _value(bridge)

	control._user_changed(1.0)
	bridge.state_changed.emit({"v": 2.0})

	assert control._status() == "mismatch"

def test_tolerance_is_configurable(qt_app):

	bridge = FakeBridge()
	control = _value(bridge, tolerance=0.0, abs_tolerance=0.0)

	control._user_changed(1.0)
	bridge.state_changed.emit({"v": 1.004})

	assert control._status() == "mismatch"
