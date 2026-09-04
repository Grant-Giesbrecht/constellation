""" The SP/PV parameter controls (`ui.py`'s Parameter* family).

These are the first tests over `ui.py` at all - it has been the one substantial module with no
coverage, which matters more here than usual because a status lamp that lies is worse than no lamp:
the entire point of the widget is that a user trusts its colour instead of checking the
instrument.

Runs headless (`QT_QPA_PLATFORM=offscreen`), with a fake bridge in place of a real instrument, so
the widget's state machine is exercised without Qt needing a display or an instrument being
present.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication

import pylogfile.base as plf

from constellation.ui import (ParameterBox, ParameterToggle, ParameterChoice, ActionIcon,
	VERIFICATION_COLORS, SEND_COLORS, VALUE_COLORS, verification_indicator,
	clear_verification_cache)
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000E_dvr import RigolDS1000E

@pytest.fixture(scope="session")
def qt_app():

	app = QApplication.instance() or QApplication([])

	yield app

class FakeBridge(QObject):
	''' An InstrumentBridge that records requests instead of talking to anything.

	Same three signals a real bridge emits, so the widget cannot tell the difference - which is
	the point: the widget is supposed to work identically behind an OwningBridge and an
	ObserverBridge.
	'''

	state_changed = pyqtSignal(object)
	command_result = pyqtSignal(str, tuple, bool, object)
	connection_changed = pyqtSignal(bool)

	def __init__(self, driver=None):
		super().__init__()

		self.driver = driver
		self.requests = []

	def request(self, method_name, *args, **kwargs):
		self.requests.append((method_name, args))

class FakeState:
	''' Minimal stand-in for the InstrumentState a real bridge emits. '''

	def __init__(self, value):
		self.value = value

def _box(bridge, **kwargs):

	return ParameterBox(bridge, "Volts/div", get=lambda s: s.value, set_method="set_div_volt",
		set_args=lambda v: (1, v), get_args=(1,), unit="V", **kwargs)

def _color(lamp) -> str:
	''' Pulls the colour back out of the lamp's stylesheet. '''

	sheet = lamp.styleSheet()
	start = sheet.index("background-color:") + len("background-color:")

	return sheet[start:sheet.index(";", start)].strip()

# --- the three lamps are independent ----------------------------------------------------------

def test_lamps_start_in_the_honest_unknown_state(qt_app):
	""" A fresh control has asked for nothing and heard nothing. Neither lamp may read green. """

	widget = _box(FakeBridge())

	assert _color(widget.lamp_send) == SEND_COLORS["unsent"]
	assert _color(widget.lamp_value) == VALUE_COLORS["unqueried"]

def test_a_successful_send_greens_only_the_send_lamp(qt_app):
	""" The value lamp must not follow the send lamp: a command can be accepted by the instrument
	and still not do what was asked. Keeping them separate is the whole reason there are three. """

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.edit.setText("2.0")
	widget.edit.editingFinished.emit()
	bridge.command_result.emit("set_div_volt", (1, 2.0), True, None)

	assert _color(widget.lamp_send) == SEND_COLORS["sent"]
	assert _color(widget.lamp_value) == VALUE_COLORS["unqueried"]

def test_a_failed_send_reds_the_send_lamp_and_says_why(qt_app):

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.edit.setText("2.0")
	widget.edit.editingFinished.emit()
	bridge.command_result.emit("set_div_volt", (1, 2.0), False, RuntimeError("timeout"))

	assert _color(widget.lamp_send) == SEND_COLORS["failed"]
	assert "timeout" in widget.lamp_send.toolTip()

def test_a_matching_readback_greens_the_value_lamp(qt_app):

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.edit.setText("2.0")
	widget.edit.editingFinished.emit()
	bridge.command_result.emit("set_div_volt", (1, 2.0), True, None)
	bridge.state_changed.emit(FakeState(2.0))

	assert _color(widget.lamp_value) == VALUE_COLORS["match"]
	assert widget.pv_display.text() == "2.0"

def test_going_offline_reds_the_value_lamp(qt_app):
	""" Not being able to read a value is a different fact from reading one that disagrees, and
	the difference is what tells a user whether to look at the cable or at the driver. """

	bridge = FakeBridge()
	widget = _box(bridge)

	bridge.state_changed.emit(FakeState(2.0))
	assert _color(widget.lamp_value) == VALUE_COLORS["match"]

	bridge.connection_changed.emit(False)
	assert _color(widget.lamp_value) == VALUE_COLORS["query_error"]

# --- tolerance --------------------------------------------------------------------------------

def test_quantization_is_not_a_mismatch(qt_app):
	""" A scope asked for 2.0 V/div may report 1.9999999. Exact equality - which the older
	single-lamp controls use - would mark a correctly working instrument as wrong forever, and a
	panel of permanently wrong lamps is a panel nobody reads. """

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.edit.setText("2.0")
	widget.edit.editingFinished.emit()
	bridge.state_changed.emit(FakeState(1.9999999))

	assert _color(widget.lamp_value) == VALUE_COLORS["match"]

def test_a_real_disagreement_is_grey_not_red(qt_app):
	""" Grey, deliberately: the instrument answered, it just answered something else. That is
	usually the instrument snapping to its own grid, not an error - and if it were coloured as one,
	the genuine errors would be lost among the false ones. """

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.edit.setText("0.55")
	widget.edit.editingFinished.emit()
	bridge.state_changed.emit(FakeState(0.5))

	assert _color(widget.lamp_value) == VALUE_COLORS["mismatch"]
	# Both numbers are on screen at once - which is the point of the two rows.
	assert widget.edit.text() == "0.55"
	assert widget.pv_display.text() == "0.5"

def test_tolerance_is_configurable(qt_app):

	bridge = FakeBridge()
	widget = _box(bridge, tolerance=0.2)

	widget.edit.setText("0.55")
	widget.edit.editingFinished.emit()
	bridge.state_changed.emit(FakeState(0.5))

	assert _color(widget.lamp_value) == VALUE_COLORS["match"]

def test_booleans_compare_as_booleans(qt_app):
	""" A relative tolerance on 0.0 is 0.0, so False/False must not fall through the numeric path
	and come out as a mismatch. """

	bridge = FakeBridge()
	widget = ParameterToggle(bridge, "Enable", get=lambda s: s.value, set_method="set_chan_enable", set_args=lambda v: (1, v))

	widget.button.setChecked(True)
	bridge.state_changed.emit(FakeState(True))
	assert _color(widget.lamp_value) == VALUE_COLORS["match"]

	widget.button.setChecked(False)
	bridge.state_changed.emit(FakeState(False))
	assert _color(widget.lamp_value) == VALUE_COLORS["match"]

# --- the SP / PV buttons ----------------------------------------------------------------------

def test_the_pv_button_re_queries(qt_app):

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.pv_icon.clicked.emit()

	assert bridge.requests == [("get_div_volt", (1,))]

def test_the_sp_button_re_sends_the_setpoint(qt_app):

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.edit.setText("2.0")
	widget.edit.editingFinished.emit()
	bridge.requests.clear()

	widget.sp_icon.clicked.emit()

	assert bridge.requests == [("set_div_volt", (1, 2.0))]

def test_the_sp_button_does_nothing_without_a_setpoint(qt_app):
	""" Clicking a refresh-looking icon must never invent a value and write it to an instrument.
	Nothing has been requested yet, so there is nothing defensible to re-send. """

	bridge = FakeBridge()
	widget = _box(bridge)

	widget.sp_icon.clicked.emit()

	assert bridge.requests == []

def test_the_getter_name_is_derived_from_the_setter(qt_app):
	""" Every category API is a set_x/get_x pair, so a widget author should not have to repeat
	the name. """

	assert _box(FakeBridge()).get_method == "get_div_volt"

def test_a_non_conventional_getter_can_be_given(qt_app):

	bridge = FakeBridge()
	widget = ParameterBox(bridge, "Thing", get=lambda s: s.value, set_method="apply_thing", get_method="read_thing")

	widget.pv_icon.clicked.emit()

	assert bridge.requests == [("read_thing", ())]

def test_no_getter_disables_the_pv_button(qt_app):

	widget = ParameterBox(FakeBridge(), "Thing", get=lambda s: s.value, set_method="apply_thing")

	assert widget.get_method is None
	assert not widget.pv_icon.isEnabled()

# --- verification lamp ------------------------------------------------------------------------

def test_verification_is_unknown_without_a_local_driver(qt_app):
	""" An ObserverBridge watches an instrument owned by another process and has no driver class
	to ask. It must say so rather than guess - guessing here would be guessing in the optimistic
	direction, which is the failure the whole verification scheme exists to prevent. """

	widget = _box(FakeBridge(driver=None))

	assert _color(widget.lamp_verification) == VERIFICATION_COLORS["unknown"]
	assert "No local driver" in widget.lamp_verification.toolTip()

def test_an_unverified_method_reads_as_untested(qt_app):

	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL
	clear_verification_cache()

	widget = _box(FakeBridge(driver=RigolDS1000Z("DUMMY", log=log, dummy=True)))

	assert _color(widget.lamp_verification) == VERIFICATION_COLORS["untested"]

def test_an_unavailable_method_is_dark_and_the_control_is_dead(qt_app):
	""" The DS1000E cannot set its timebase over SCPI. Offering a live control that raises
	FeatureUnavailable when used is worse than offering none. """

	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL
	clear_verification_cache()

	bridge = FakeBridge(driver=RigolDS1000E("DUMMY", log=log, dummy=True))
	widget = ParameterBox(bridge, "Time/div", get=lambda s: s.value, set_method="set_div_time")

	assert _color(widget.lamp_verification) == VERIFICATION_COLORS["unavailable"]
	assert not widget.edit.isEnabled()
	assert not widget.sp_icon.isEnabled()
	assert "cannot set the timebase" in widget.lamp_verification.toolTip()

def test_the_weaker_half_of_a_set_get_pair_wins(qt_app, monkeypatch):
	""" Neither half of a set/get pair can be verified without the other, so a confirmed setter
	paired with an unverified getter is not a verified parameter. Fail closed. """

	import constellation.ui as ui
	from constellation.verification import VerificationStatus

	clear_verification_cache()
	monkeypatch.setattr(ui, "_verification_report", lambda cls, idn=None: {
		"set_div_volt": {"status": VerificationStatus.CONFIRMED, "detail": None, "trusted": True},
		"get_div_volt": {"status": VerificationStatus.UNVERIFIED, "detail": None, "trusted": False},
	})

	key, tip = verification_indicator(FakeBridge(driver=object()), ("set_div_volt", "get_div_volt"))

	assert key == "untested"
	# ...and the tooltip still shows both, so the user can see which half is the weak one.
	assert "set_div_volt()" in tip and "get_div_volt()" in tip

def test_a_stale_record_never_reads_as_verified(qt_app, monkeypatch):
	""" The case the staleness layer exists for: verified on hardware, then the code changed. A
	green lamp here would be the exact lie the whole scheme is built to prevent. """

	import constellation.ui as ui
	from constellation.verification import VerificationStatus

	clear_verification_cache()
	monkeypatch.setattr(ui, "_verification_report", lambda cls, idn=None: {
		"set_div_volt": {"status": VerificationStatus.STALE_CODE, "detail": {"date": "2026-01-01"}, "trusted": False},
	})

	key, tip = verification_indicator(FakeBridge(driver=object()), ("set_div_volt",))

	assert key == "untested"
	assert "stale-code" in tip

def test_broken_beats_untested(qt_app, monkeypatch):
	""" 'We tried this and it did not work' must not be outranked into silence by a neighbouring
	method nobody has tried. """

	import constellation.ui as ui
	from constellation.verification import VerificationStatus

	clear_verification_cache()
	monkeypatch.setattr(ui, "_verification_report", lambda cls, idn=None: {
		"set_x": {"status": VerificationStatus.FAILED, "detail": None, "trusted": False},
		"get_x": {"status": VerificationStatus.UNVERIFIED, "detail": None, "trusted": False},
	})

	key, _ = verification_indicator(FakeBridge(driver=object()), ("set_x", "get_x"))

	assert key == "broken"

def test_a_broken_records_file_does_not_take_the_gui_down(qt_app, monkeypatch):
	""" A GUI that refuses to open because a YAML file is malformed has turned a bookkeeping
	problem into an outage. """

	import constellation.verification as cv

	clear_verification_cache()
	monkeypatch.setattr(cv, "capability_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad yaml")))

	widget = _box(FakeBridge(driver=object()))

	assert _color(widget.lamp_verification) == VERIFICATION_COLORS["unknown"]

	clear_verification_cache()

# --- the other two control types ---------------------------------------------------------------

def test_choice_shows_setpoint_and_readback_separately(qt_app):
	""" An instrument that silently refuses an unsupported mode looks identical to one that
	accepted it, until its actual answer is on screen next to what was asked for. """

	bridge = FakeBridge()
	widget = ParameterChoice(bridge, "Coupling", get=lambda s: s.value, set_method="set_coupling",
		set_args=lambda v: (1, v), choices=["coup-ac", "coup-dc"])

	widget.combo.setCurrentIndex(0)
	widget.combo.activated.emit(0)
	bridge.state_changed.emit(FakeState("coup-dc"))

	assert widget.pv_display.text() == "coup-dc"
	assert _color(widget.lamp_value) == VALUE_COLORS["mismatch"]

def test_toggle_reports_what_the_instrument_actually_says(qt_app):

	bridge = FakeBridge()
	widget = ParameterToggle(bridge, "Enable", get=lambda s: s.value, set_method="set_chan_enable",
		set_args=lambda v: (1, v), on_text="ON", off_text="OFF")

	widget.button.setChecked(True)
	assert bridge.requests == [("set_chan_enable", (1, True))]

	bridge.state_changed.emit(FakeState(False))

	assert widget.pv_display.text() == "OFF"
	assert _color(widget.lamp_value) == VALUE_COLORS["mismatch"]

def test_action_icons_are_clickable_and_carry_tooltips(qt_app):

	widget = _box(FakeBridge())

	for icon in (widget.sp_icon, widget.pv_icon):
		assert isinstance(icon, ActionIcon)
		assert icon.toolTip()
