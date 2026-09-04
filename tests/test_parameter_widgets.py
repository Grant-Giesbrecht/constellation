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
	IndicatorLight, StatusLamp, ParameterView, ParameterDetailDialog, VERIFICATION_COLORS,
	SEND_COLORS, VALUE_COLORS, verification_indicator, clear_verification_cache)
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

	kwargs.setdefault("view", ParameterView.FULL)

	return ParameterBox(bridge, "Volts/div", get=lambda s: s.value, set_method="set_div_volt",
		set_args=lambda v: (1, v), get_args=(1,), unit="V", **kwargs)

def _color(lamp) -> str:
	return lamp.color

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
	widget = ParameterToggle(bridge, "Enable", get=lambda s: s.value, set_method="set_chan_enable", set_args=lambda v: (1, v), view=ParameterView.FULL)

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
	widget = ParameterBox(bridge, "Thing", get=lambda s: s.value, set_method="apply_thing", get_method="read_thing", view=ParameterView.FULL)

	widget.pv_icon.clicked.emit()

	assert bridge.requests == [("read_thing", ())]

def test_no_getter_disables_the_pv_button(qt_app):

	widget = ParameterBox(FakeBridge(), "Thing", get=lambda s: s.value, set_method="apply_thing", view=ParameterView.FULL)

	assert widget.get_method is None
	assert not widget.pv_icon.isEnabled()

# --- verification lamp ------------------------------------------------------------------------

def test_verification_is_unknown_without_a_local_driver(qt_app):
	""" An ObserverBridge watches an instrument owned by another process and has no driver class
	to ask. It must say so rather than guess - guessing here would be guessing in the optimistic
	direction, which is the failure the whole verification scheme exists to prevent. """

	widget = _box(FakeBridge(driver=None))

	assert _color(widget.lamp_verification) == VERIFICATION_COLORS["unknown"]
	assert "no local driver" in widget.lamp_verification.toolTip()

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
	widget = ParameterBox(bridge, "Time/div", get=lambda s: s.value, set_method="set_div_time", view=ParameterView.FULL)

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

	key, lines = verification_indicator(FakeBridge(driver=object()), ("set_div_volt", "get_div_volt"))

	assert key == "untested"
	# ...and both halves are still reported, so the user can see which one is the weak one.
	assert any("set_div_volt()" in line for line in lines)
	assert any("get_div_volt()" in line for line in lines)

def test_a_stale_record_never_reads_as_verified(qt_app, monkeypatch):
	""" The case the staleness layer exists for: verified on hardware, then the code changed. A
	green lamp here would be the exact lie the whole scheme is built to prevent. """

	import constellation.ui as ui
	from constellation.verification import VerificationStatus

	clear_verification_cache()
	monkeypatch.setattr(ui, "_verification_report", lambda cls, idn=None: {
		"set_div_volt": {"status": VerificationStatus.STALE_CODE, "detail": {"date": "2026-01-01"}, "trusted": False},
	})

	key, lines = verification_indicator(FakeBridge(driver=object()), ("set_div_volt",))

	assert key == "untested"
	assert any("stale-code" in line for line in lines)

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
		set_args=lambda v: (1, v), choices=["coup-ac", "coup-dc"], view=ParameterView.FULL)

	widget.combo.setCurrentIndex(0)
	widget.combo.activated.emit(0)
	bridge.state_changed.emit(FakeState("coup-dc"))

	assert widget.pv_display.text() == "coup-dc"
	assert _color(widget.lamp_value) == VALUE_COLORS["mismatch"]

def test_toggle_reports_what_the_instrument_actually_says(qt_app):

	bridge = FakeBridge()
	widget = ParameterToggle(bridge, "Enable", get=lambda s: s.value, set_method="set_chan_enable",
		set_args=lambda v: (1, v), on_text="ON", off_text="OFF", view=ParameterView.FULL)

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

# --- display modes ------------------------------------------------------------------------------

def test_full_view_shows_everything(qt_app):

	widget = _box(FakeBridge(), view=ParameterView.FULL)

	assert widget.pv_display.isVisible() or not widget.isVisible()   # hidden only because unshown
	assert widget.visible_lamps() == [widget.lamp_verification, widget.lamp_send, widget.lamp_value]

def test_compact_view_drops_the_measured_row_and_merges_two_lamps(qt_app):
	""" The default. A front panel is mostly settings you are not currently suspicious of. """

	widget = _box(FakeBridge(), view=ParameterView.COMPACT)

	assert widget.visible_lamps() == [widget.lamp_verification, widget.lamp_merged]

def test_minimal_view_is_one_lamp(qt_app):

	widget = _box(FakeBridge(), view=ParameterView.MINIMAL)

	assert widget.visible_lamps() == [widget.lamp_merged]

def test_compact_is_the_default(qt_app):

	widget = ParameterBox(FakeBridge(), "V/div", get=lambda s: s.value, set_method="set_div_volt")

	assert widget.view == ParameterView.COMPACT

def test_the_view_can_be_switched_live_without_losing_state(qt_app):
	""" The point of the density switch: turn a control up *while* something looks wrong, without
	rebuilding the panel or losing what it already knows. """

	bridge = FakeBridge()
	widget = _box(bridge, view=ParameterView.MINIMAL)

	widget.edit.setText("2.0")
	widget.edit.editingFinished.emit()
	bridge.state_changed.emit(FakeState(2.0))

	widget.set_view(ParameterView.FULL)

	assert widget.view == ParameterView.FULL
	assert widget.setpoint == 2.0
	assert widget.pv_display.text() == "2.0"
	assert widget.visible_lamps() == [widget.lamp_verification, widget.lamp_send, widget.lamp_value]

	# ...and back down again, repeatedly, without the layout accumulating anything.
	for mode in (ParameterView.MINIMAL, ParameterView.COMPACT, ParameterView.FULL):
		widget.set_view(mode)

	assert widget.setpoint == 2.0

def test_an_unknown_view_is_ignored(qt_app):

	widget = _box(FakeBridge(), view=ParameterView.COMPACT)
	widget.set_view("enormous")

	assert widget.view == ParameterView.COMPACT

def test_the_merged_lamp_takes_the_worst_contributing_status(qt_app):
	""" Compressing the display must not hide a problem - only the explanation of which problem,
	which the tooltip and the detail window still carry. """

	bridge = FakeBridge()
	widget = _box(bridge, view=ParameterView.COMPACT)

	# Sent fine, but the instrument disagrees -> the merged lamp must show the disagreement.
	widget.edit.setText("0.55")
	widget.edit.editingFinished.emit()
	bridge.command_result.emit("set_div_volt", (1, 0.55), True, None)
	bridge.state_changed.emit(FakeState(0.5))

	assert _color(widget.lamp_merged) == VALUE_COLORS["mismatch"]

	# A hard failure outranks a disagreement.
	bridge.command_result.emit("set_div_volt", (1, 0.55), False, RuntimeError("boom"))
	assert _color(widget.lamp_merged) == SEND_COLORS["failed"]

def test_the_merged_tooltip_still_names_every_status_it_stands_for(qt_app):

	widget = _box(FakeBridge(), view=ParameterView.COMPACT)
	tip = widget.lamp_merged.toolTip()

	assert "Setpoint:" in tip and "Measured:" in tip

# --- lamps are clickable, and the detail window ---------------------------------------------------

def test_lamps_carry_no_stylesheet(qt_app):
	""" Regression: the lamp used to be a styled QLabel whose stylesheet capped width at 11px.
	Qt resolves a widget's stylesheet for its tooltip window too, so the tooltip inherited the cap
	and rendered as one clipped letter. Painting means there is nothing to inherit. """

	widget = _box(FakeBridge())

	for lamp in (widget.lamp_verification, widget.lamp_send, widget.lamp_value, widget.lamp_merged):
		assert lamp.styleSheet() == ""
		assert lamp.toolTip()

def test_clicking_a_lamp_opens_the_detail_window(qt_app):

	widget = _box(FakeBridge())
	assert widget._dialog is None

	widget.lamp_verification.clicked.emit()

	assert isinstance(widget._dialog, ParameterDetailDialog)

def test_all_lamps_open_the_same_window(qt_app):

	widget = _box(FakeBridge())

	widget.lamp_send.clicked.emit()
	first = widget._dialog
	widget.lamp_value.clicked.emit()

	assert widget._dialog is first

def test_the_detail_window_switches_the_view(qt_app):

	widget = _box(FakeBridge(), view=ParameterView.MINIMAL)
	widget.show_details()

	dialog = widget._dialog
	dialog.view_combo.setCurrentIndex(ParameterView.ORDER.index(ParameterView.FULL))
	dialog.view_combo.activated.emit(ParameterView.ORDER.index(ParameterView.FULL))

	assert widget.view == ParameterView.FULL

def test_the_detail_window_follows_live_changes(qt_app):
	""" A window left open while the instrument is poked has to keep telling the truth. """

	bridge = FakeBridge()
	widget = _box(bridge)
	widget.show_details()

	bridge.command_result.emit("set_div_volt", (1, 2.0), False, RuntimeError("VI_ERROR_TMO"))

	assert "VI_ERROR_TMO" in widget._dialog.traffic.text() + widget._dialog.lamp_rows["send"][1].text()

def test_the_detail_window_shows_the_driver_call_and_the_scpi(qt_app):

	bridge = FakeBridge()
	bridge.last_scpi = {"set_div_volt": (":CHAN1:SCAL 2.0", None)}

	widget = _box(bridge)
	widget.edit.setText("2.0")
	widget.edit.editingFinished.emit()
	widget.show_details()

	text = widget._dialog.traffic.text()

	assert "set_div_volt(1, 2.0)" in text
	assert ":CHAN1:SCAL 2.0" in text

def test_the_detail_window_says_when_there_is_no_scpi(qt_app):
	""" A dummy driver never touches a relay and an ObserverBridge issues the call in another
	process - neither is an error, and neither should show a blank field. """

	widget = _box(FakeBridge())
	widget.show_details()

	assert "No SCPI recorded" in widget._dialog.traffic.text()

def test_the_detail_window_will_not_resend_without_a_setpoint(qt_app):

	widget = _box(FakeBridge())
	widget.show_details()

	assert not widget._dialog.resend_button.isEnabled()

# --- the toggle's indicator lamp -----------------------------------------------------------------

def test_the_toggle_indicator_follows_the_instrument_not_the_button(qt_app):
	""" A button that was clicked and did nothing must be visible as such. The lamp shows what was
	read back, so it stays dark until the instrument agrees. """

	bridge = FakeBridge()
	widget = ParameterToggle(bridge, "Output", get=lambda s: s.value, set_method="set_output_enable",
		set_args=lambda v: (1, v))

	assert widget.indicator._state is None

	widget.button.setChecked(True)
	assert widget.indicator._state is None      # asked, not yet confirmed

	bridge.state_changed.emit(FakeState(False))
	assert widget.indicator._state is False     # the instrument said no

	bridge.state_changed.emit(FakeState(True))
	assert widget.indicator._state is True

def test_the_toggle_indicator_uses_the_packaged_artwork(qt_app):
	""" If this fails with both pixmaps None, `assets/` did not ship - which is a live packaging
	bug, not a widget bug (see todo P18). The widget still works: it falls back to a painted
	circle rather than becoming an invisible control. """

	light = IndicatorLight()

	assert light._on is not None and light._off is not None

def test_the_toggle_indicator_can_be_overridden(qt_app):

	from PyQt6.QtGui import QPixmap

	custom = QPixmap(8, 8)
	custom.fill()

	light = IndicatorLight(on_pixmap=custom, off_pixmap=custom)

	assert light._on is not None

def test_a_missing_pixmap_falls_back_instead_of_crashing(qt_app):

	light = IndicatorLight(on_pixmap="/nonexistent/nope.png", off_pixmap="/nonexistent/nope.png")
	light.set_state(True)

	assert light._on is None    # and paintEvent draws a circle instead

# --- resize behaviour ----------------------------------------------------------------------------

def test_the_control_refuses_to_stretch(qt_app):
	""" Extra space in a container must go BETWEEN controls, not inside them. Without this, a
	resized panel re-spaces every control's internals and the whole layout crawls. """

	from PyQt6.QtWidgets import QSizePolicy

	widget = _box(FakeBridge())

	assert widget.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Maximum
	assert widget.edit.width() == widget.edit_width

# --- SCPI attribution -----------------------------------------------------------------------------

def test_the_bridge_attributes_scpi_to_the_method_that_sent_it(qt_app):
	""" The relay only knows the most recent command *globally*, so reading it live would credit
	one control's SCPI to whichever control happened to ask last. The bridge journals it per
	method at the moment the call returns. """

	from constellation.ui import OwningBridge

	class FakeRelay:
		def __init__(self):
			self.last_command = None
			self.last_response = None

	class FakeDriver:
		def __init__(self):
			self.relay = FakeRelay()

		def set_div_volt(self, channel, value):
			self.relay.last_command = f":CHAN{channel}:SCAL {value}"
			self.relay.last_response = None

		def get_div_volt(self, channel):
			self.relay.last_command = f":CHAN{channel}:SCAL?"
			self.relay.last_response = "2.000000e+00"

		def poll(self):
			raise RuntimeError("no state in this fake")

	bridge = OwningBridge(FakeDriver())

	bridge._execute("set_div_volt", (1, 2.0), {})
	bridge._execute("get_div_volt", (1,), {})

	assert bridge.last_scpi["set_div_volt"] == (":CHAN1:SCAL 2.0", None)
	assert bridge.last_scpi["get_div_volt"] == (":CHAN1:SCAL?", "2.000000e+00")

def test_scpi_is_journalled_even_when_the_call_fails(qt_app):
	""" A failed call is exactly when someone wants to see what was actually sent. """

	from constellation.ui import OwningBridge

	class FakeRelay:
		last_command = ":CHAN1:SCAL 2.0"
		last_response = None

	class FakeDriver:
		relay = FakeRelay()
		def set_div_volt(self, *a):
			raise RuntimeError("VI_ERROR_TMO")
		def poll(self):
			raise RuntimeError("no state in this fake")

	bridge = OwningBridge(FakeDriver())
	bridge._execute("set_div_volt", (1, 2.0), {})

	assert bridge.last_scpi["set_div_volt"][0] == ":CHAN1:SCAL 2.0"

def test_a_long_response_is_truncated_before_being_stored():
	""" A full-memory waveform read returns hundreds of thousands of points, none of which belong
	in a tooltip - or in memory held indefinitely by a relay. """

	from constellation.relay import CommandRelay

	relay = CommandRelay()
	relay.note_command(":WAV:DATA?", "x" * 5000)

	assert len(relay.last_response) < 300
	assert "5000 chars" in relay.last_response
