""" The Parameter* status lamps: the packaged artwork or the painted dots, which file each status
draws, and the placeholder for a value nothing has read yet.

The icon choice is tested through StatusLamp.icon_name(), which is the whole decision - drawing is
just loading the file it names. One test checks every file any status can name exists, so missing
artwork is reported here rather than silently falling back in front of a user.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

import pylogfile.base as plf

from PyQt6.QtWidgets import QApplication

from constellation.ui import (ASSETS_DIR, LampStyle, DEFAULT_LAMP_STYLE, LAMP_ICON_COLORS,
	StatusLamp, ParameterBox, ParameterToggle, ParameterChoice, ParameterView,
	VERIFICATION_COLORS, VALUE_COLORS, SEND_COLORS)

from test_parameter_widgets import FakeBridge, FakeState

@pytest.fixture(scope="module")
def qt_app():

	yield QApplication.instance() or QApplication([])

def _box(bridge=None, **kwargs):

	return ParameterBox(bridge or FakeBridge(), "V/div", get=lambda s: s.value,
		set_method="set_div_volt", set_args=lambda v: (1, v), **kwargs)

# --- which artwork ------------------------------------------------------------------------------

@pytest.mark.parametrize("role,status,expected", [
	("verification", "confirmed", "indicator_v_green"),
	("verification", "roundtrip", "indicator_v_blue"),
	("verification", "untested", "indicator_v_yellow"),
	("verification", "broken", "indicator_v_red"),
	("verification", "unavailable", "indicator_v_pink"),
	("verification", "unknown", "indicator_v_grey"),
	("send", "sent", "indicator_up_green"),
	("send", "unsent", "indicator_up_yellow"),
	("send", "failed", "indicator_up_red"),
	("value", "match", "indicator_down_green"),
	("value", "unqueried", "indicator_down_yellow"),
	("value", "mismatch", "indicator_down_grey"),
	("value", "query_error", "indicator_down_red"),
	("value", "unknown", "indicator_idk"),
])
def test_each_status_names_its_artwork(qt_app, role, status, expected):

	lamp = StatusLamp(role=role, style=LampStyle.ICONS)
	lamp.set("#000000", "", status)

	assert lamp.icon_name() == expected

def test_the_unavailable_lamp_is_pink():
	""" Was dark grey, which read as "off" rather than "this instrument cannot do it". """

	assert VERIFICATION_COLORS["unavailable"] == "#e87fa6"

def _artwork_for(role, status) -> str:

	lamp = StatusLamp(role=role, style=LampStyle.ICONS)
	lamp.set("#000000", "", status)

	return os.path.join(ASSETS_DIR, f"{lamp.icon_name()}.png")

def test_every_status_has_artwork(qt_app):
	""" A missing file falls back to a painted dot rather than an empty gap - but it should be
	reported here, not discovered in front of an instrument. """

	missing = [f"{role}/{status}" for role, statuses in LAMP_ICON_COLORS.items() for status in statuses
		if not os.path.exists(_artwork_for(role, status))]

	assert missing == []

def test_a_missing_file_falls_back_to_the_painted_dot(qt_app, monkeypatch):

	import constellation.ui as ui

	lamp = StatusLamp(role="value", style=LampStyle.ICONS)
	lamp.set(VALUE_COLORS["match"], "", "match")
	monkeypatch.setattr(ui, "_LAMP_PIXMAPS", {})
	monkeypatch.setattr(ui, "ASSETS_DIR", "/nonexistent")

	assert lamp.icon_name() == "indicator_down_green"   # it still knows what it wanted
	assert lamp.pixmap() is None                        # ...and paints a dot instead

# --- the two styles -----------------------------------------------------------------------------

def test_icons_are_the_default(qt_app):

	assert DEFAULT_LAMP_STYLE == LampStyle.ICONS
	assert _box().lamp_value.pixmap() is not None

def test_a_control_can_ask_for_the_painted_dots(qt_app):

	painted = _box(lamp_style=LampStyle.PAINTED)

	assert painted.lamp_value.pixmap() is None
	assert painted.lamp_value.color == VALUE_COLORS[painted.value_status()]

@pytest.mark.parametrize("factory", [
	lambda bridge, style: ParameterBox(bridge, "V/div", get=lambda s: s.value,
		set_method="set_div_volt", lamp_style=style),
	lambda bridge, style: ParameterToggle(bridge, "Output", get=lambda s: s.value,
		set_method="set_output_enable", lamp_style=style),
	lambda bridge, style: ParameterChoice(bridge, "Coupling", get=lambda s: s.value,
		set_method="set_coupling", choices=["ac", "dc"], lamp_style=style),
])
def test_every_control_takes_a_style(qt_app, factory):

	control = factory(FakeBridge(), LampStyle.PAINTED)

	assert control.lamp_style == LampStyle.PAINTED
	assert control.lamp_send.pixmap() is None

def test_the_detail_window_draws_in_the_controls_style(qt_app):

	control = _box(lamp_style=LampStyle.PAINTED)
	control.show_details()
	lamp, _now = control._dialog.lamp_rows["value"]

	assert lamp.pixmap() is None
	assert control._dialog.legend_entries["value"]["match"][0].pixmap() is None

	control._dialog.close()

# --- a value nothing has read yet ---------------------------------------------------------------

def test_a_control_that_has_heard_nothing_shows_the_placeholder(qt_app):
	""" The panel is drawn before the instrument answers; those cells are blanks, not readings. """

	widget = _box()

	assert widget.value_status() == "unknown"
	assert widget.lamp_value.icon_name() == "indicator_idk"

def test_a_reading_replaces_the_placeholder(qt_app):

	bridge = FakeBridge()
	widget = _box(bridge)
	bridge.state_changed.emit(FakeState(2.0))

	assert widget.value_status() == "match"
	assert widget.lamp_value.icon_name() == "indicator_down_green"

def test_waiting_for_a_readback_is_not_the_same_as_never_having_had_one(qt_app):
	""" Yellow "asked, nothing back yet" versus the placeholder "never read". """

	bridge = FakeBridge()
	widget = _box(bridge)
	bridge.state_changed.emit(FakeState(2.0))
	widget._user_changed(3.0)

	assert widget.value_status() == "unqueried"
	assert widget.lamp_value.icon_name() == "indicator_down_yellow"
