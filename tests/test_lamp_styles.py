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

# --- how the lamps are arranged, per style ------------------------------------------------------

def test_painted_dots_stack_beside_the_rows(qt_app):
	""" Small enough to sit in a column next to the field they annotate. """

	control = _box(lamp_style=LampStyle.PAINTED)

	assert control.lamps_vertical()
	assert control.lamp_value._box == 11

def test_icons_sit_in_a_row_and_match_the_toggle_indicator(qt_app):
	""" Three stacked would make a control three lamps tall, and the artwork needs the same room as
	a ParameterToggle's indicator so every indicator on a panel is one size. """

	from constellation.ui import ParameterToggle

	control = _box(lamp_style=LampStyle.ICONS)
	toggle = ParameterToggle(FakeBridge(), "Output", get=lambda s: s.value, set_method="set_output_enable")

	assert not control.lamps_vertical()          # compact: a row
	assert control.lamp_value._box == 22 == toggle.indicator._size

def test_icon_lamps_line_up_horizontally(qt_app):

	control = _box(lamp_style=LampStyle.ICONS, view=ParameterView.COMPACT)
	control.show()
	qt_app.processEvents()

	lamps = control.visible_lamps()
	assert len({lamp.geometry().top() for lamp in lamps}) == 1        # one row
	assert len({lamp.geometry().left() for lamp in lamps}) == len(lamps)

	control.hide()

def test_painted_lamps_line_up_vertically(qt_app):

	control = _box(lamp_style=LampStyle.PAINTED, view=ParameterView.FULL)
	control.show()
	qt_app.processEvents()

	lamps = control.visible_lamps()
	assert len({lamp.geometry().left() for lamp in lamps}) == 1       # one column
	assert len({lamp.geometry().top() for lamp in lamps}) == len(lamps)

	control.hide()

# --- the minimal density ------------------------------------------------------------------------

def test_minimal_shows_only_the_measurement_lamp(qt_app):
	""" The one question a panel is watched for: does the instrument agree with what it was asked. """

	control = _box(view=ParameterView.MINIMAL)

	assert control.visible_lamps() == [control.lamp_value]

def test_minimal_keeps_the_inline_label(qt_app):
	""" There is no title in either compact view, so the field needs its name beside it. """

	control = _box(view=ParameterView.MINIMAL)

	assert control.show_inline_label()
	assert not control.show_sp_icon()

def test_a_minimal_toggle_still_says_what_it_is(qt_app):

	from constellation.ui import ParameterToggle

	toggle = ParameterToggle(FakeBridge(), "Output 1", get=lambda s: s.value,
		set_method="set_output_enable", view=ParameterView.MINIMAL)

	assert toggle.button.text() == "Output 1"

def test_the_densities_run_least_to_most(qt_app):

	assert ParameterView.ORDER == (ParameterView.MINIMAL, ParameterView.COMPACT, ParameterView.FULL)
	assert ParameterView.LABELS[ParameterView.MINIMAL] == "Minimal"

def test_switching_down_to_minimal_in_place(qt_app):

	control = _box(view=ParameterView.FULL)
	control.set_view(ParameterView.MINIMAL)

	assert control.view == ParameterView.MINIMAL
	assert control.visible_lamps() == [control.lamp_value]


# --- which way the lamps run --------------------------------------------------------------------

@pytest.mark.parametrize("view,vertical", [
	(ParameterView.FULL, True),        # already three rows tall - a column keeps each lamp beside
	(ParameterView.COMPACT, False),    # ...its rows. One row of fields: the lamps go beside it.
	(ParameterView.MINIMAL, False),
])
def test_icons_stack_only_in_the_full_view(qt_app, view, vertical):

	assert _box(lamp_style=LampStyle.ICONS, view=view).lamps_vertical() is vertical

@pytest.mark.parametrize("view", list(ParameterView.ORDER))
def test_painted_dots_stack_in_every_view(qt_app, view):
	""" Small enough that a column never makes a control taller than its rows. """

	assert _box(lamp_style=LampStyle.PAINTED, view=view).lamps_vertical() is True

def test_icon_lamps_stack_in_the_full_view(qt_app):

	control = _box(lamp_style=LampStyle.ICONS, view=ParameterView.FULL)
	control.show()
	qt_app.processEvents()

	lamps = control.visible_lamps()

	assert len({lamp.geometry().left() for lamp in lamps}) == 1       # one column
	assert len({lamp.geometry().top() for lamp in lamps}) == len(lamps)

	control.hide()

def test_switching_density_re_orients_the_lamps(qt_app):
	""" The container itself changes between a column and a row, so the switch has to survive
	being made live - which is how a user meets it, by clicking a lamp. """

	control = _box(lamp_style=LampStyle.ICONS, view=ParameterView.COMPACT)
	control.show()
	qt_app.processEvents()

	assert len({lamp.geometry().top() for lamp in control.visible_lamps()}) == 1

	control.set_view(ParameterView.FULL)
	qt_app.processEvents()

	lamps = control.visible_lamps()
	assert control.lamps_vertical()
	assert len({lamp.geometry().left() for lamp in lamps}) == 1
	assert len({lamp.geometry().top() for lamp in lamps}) == len(lamps)

	control.set_view(ParameterView.MINIMAL)
	qt_app.processEvents()

	assert not control.lamps_vertical()
	assert control.visible_lamps() == [control.lamp_value]

	control.hide()
