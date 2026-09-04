""" Window chrome: keyboard shortcuts, collapsible panels, resizable splitters.

Headless (`QT_QPA_PLATFORM=offscreen`). None of this needs an instrument - a `CollapsiblePanel`
knows nothing about drivers, and the shortcuts are pure Qt wiring.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

import pylogfile.base as plf

from constellation.ui import (ConstellationWindow, CollapsiblePanel, make_splitter,
	install_window_shortcuts, _QT_MAX_SIZE)

@pytest.fixture(scope="session")
def qt_app():

	app = QApplication.instance() or QApplication([])

	yield app

@pytest.fixture
def log():

	pile = plf.LogPile()
	pile.terminal_level = plf.CRITICAL

	return pile

def _sequences(widget) -> list:
	''' Every shortcut key sequence installed on a widget, as portable text. '''

	from PyQt6.QtGui import QShortcut

	return [s.key().toString(QKeySequence.SequenceFormat.PortableText)
		for s in widget.findChildren(QShortcut)]

# --- shortcuts ------------------------------------------------------------------------------------

def test_a_window_gets_close_and_quit_shortcuts(qt_app):

	window = QWidget()
	install_window_shortcuts(window)

	sequences = _sequences(window)

	assert any("W" in s for s in sequences), sequences
	assert any("Q" in s for s in sequences), sequences

def test_the_shortcuts_exist_even_where_the_platform_supplies_none(qt_app):
	""" QKeySequence.keyBindings(Quit) returns nothing on Windows, where quitting is conventionally
	menu-only. Constellation wants the shortcut everywhere, so an explicit fallback is appended
	whenever the platform doesn't provide one. """

	from constellation.ui import _key_bindings

	for standard, fallback in ((QKeySequence.StandardKey.Close, "Ctrl+W"),
			(QKeySequence.StandardKey.Quit, "Ctrl+Q")):

		bindings = _key_bindings(standard, fallback)

		assert bindings
		assert any(b.matches(QKeySequence(fallback)) == QKeySequence.SequenceMatch.ExactMatch
			for b in bindings), f"{standard} lost its fallback"

def test_shortcuts_are_scoped_to_their_own_window(qt_app):
	""" A dialog's Cmd-W must close the dialog, not the panel behind it. """

	from PyQt6.QtGui import QShortcut

	window = QWidget()
	install_window_shortcuts(window)

	for shortcut in window.findChildren(QShortcut):
		assert shortcut.context() == Qt.ShortcutContext.WindowShortcut

def test_the_main_window_installs_them_without_a_menu_bar(qt_app, log):
	""" The menu is optional, and on some platforms a menu action's shortcut is not live until the
	menu has been shown - so the shortcuts are installed directly, not via the menu. """

	window = ConstellationWindow(log, add_menu=False)

	sequences = _sequences(window)

	assert any("W" in s for s in sequences)
	assert any("Q" in s for s in sequences)

def test_close_window_does_not_kill_the_application(qt_app, log):
	""" Regression: the File menu's "Close Window" called sys.exit(0) straight after close(), so
	closing one instrument's window took every other panel down with it. Quit is a separate action
	now. """

	window = ConstellationWindow(log)

	window._basic_menu_close()      # must not raise SystemExit

	assert not window.isVisible()
	assert window.quit_act is not window.close_window_act

def test_the_detail_dialog_closes_on_the_close_shortcut(qt_app):

	from PyQt6.QtGui import QShortcut
	from constellation.ui import ParameterBox, ParameterView
	from PyQt6.QtCore import QObject, pyqtSignal

	class FakeBridge(QObject):
		state_changed = pyqtSignal(object)
		command_result = pyqtSignal(str, tuple, bool, object)
		connection_changed = pyqtSignal(bool)
		driver = None
		def request(self, *a, **k): pass

	control = ParameterBox(FakeBridge(), "V/div", get=lambda s: s.value, set_method="set_div_volt")
	control.show_details()
	dialog = control._dialog
	dialog.show()

	assert _sequences(dialog)

	# The close shortcut is wired to reject(), which is what actually dismisses a QDialog.
	dialog.reject()

	assert not dialog.isVisible()

# --- collapsible panels ---------------------------------------------------------------------------

def _panel(fold=Qt.Orientation.Vertical, collapsed=False) -> CollapsiblePanel:

	panel = CollapsiblePanel("Trigger", collapsed=collapsed, fold=fold)

	layout = QVBoxLayout()
	layout.addWidget(QLabel("some contents"))
	layout.addWidget(QLabel("more contents"))
	panel.set_content_layout(layout)

	return panel

def test_a_panel_starts_expanded_by_default(qt_app):

	panel = _panel()

	assert not panel.collapsed
	assert panel.content.isVisibleTo(panel)

def test_collapsing_hides_the_contents(qt_app):

	panel = _panel()
	panel.set_collapsed(True)

	assert panel.collapsed
	assert not panel.content.isVisibleTo(panel)

def test_collapsing_actually_frees_the_space(qt_app):
	""" Hiding the contents alone is not enough - a splitter keeps holding the old size, so the
	panel becomes a blank gap instead of giving its room to its neighbours. Clamping the maximum
	is what makes the space move. """

	panel = _panel()
	expanded_max = panel.maximumHeight()

	panel.set_collapsed(True)

	assert panel.maximumHeight() < expanded_max
	assert panel.maximumHeight() >= panel.header.sizeHint().height()

def test_a_sideways_panel_gives_back_width_instead(qt_app):
	""" A panel in a row of columns (a per-channel strip) must surrender its WIDTH, or collapsing
	it just leaves an empty column where it was. """

	panel = _panel(fold=Qt.Orientation.Horizontal)
	panel.set_collapsed(True)

	assert panel.maximumWidth() < _QT_MAX_SIZE
	assert panel.maximumHeight() == _QT_MAX_SIZE

def test_expanding_restores_the_clamp(qt_app):

	for fold in (Qt.Orientation.Vertical, Qt.Orientation.Horizontal):

		panel = _panel(fold=fold)
		panel.set_collapsed(True)
		panel.set_collapsed(False)

		assert panel.maximumHeight() == _QT_MAX_SIZE
		assert panel.maximumWidth() == _QT_MAX_SIZE

def test_the_header_arrow_follows_the_state(qt_app):

	panel = _panel()
	assert panel.header.arrowType() == Qt.ArrowType.DownArrow

	panel.set_collapsed(True)
	assert panel.header.arrowType() == Qt.ArrowType.RightArrow

def test_clicking_the_header_toggles_and_reports(qt_app):

	panel = _panel()
	seen = []
	panel.toggled.connect(seen.append)

	panel.header.click()
	panel.header.click()

	assert seen == [True, False]
	assert not panel.collapsed

def test_setting_the_same_state_is_a_no_op(qt_app):

	panel = _panel()
	seen = []
	panel.toggled.connect(seen.append)

	panel.set_collapsed(False)

	assert seen == []

def test_a_panel_can_start_collapsed(qt_app):

	panel = _panel(collapsed=True)

	assert panel.collapsed
	assert panel.header.arrowType() == Qt.ArrowType.RightArrow

# --- splitters -------------------------------------------------------------------------------------

def test_the_splitter_handle_is_visible_enough_to_grab(qt_app):
	""" Qt's default handle is a 1px hairline nobody discovers, so panels look fixed even when they
	are not. """

	splitter = make_splitter(Qt.Orientation.Horizontal, QLabel("a"), QLabel("b"))

	assert splitter.handleWidth() >= 4

def test_children_cannot_be_dragged_out_of_existence(qt_app):
	""" The panel header is the honest way to fold something away; a child dragged to zero just
	looks broken. """

	splitter = make_splitter(Qt.Orientation.Horizontal, QLabel("a"), QLabel("b"))

	assert not splitter.childrenCollapsible()

def test_stretch_factors_are_applied_not_pixel_sizes(qt_app):
	""" setSizes([3, 1]) means three pixels and one pixel, not a 3:1 ratio - Qt then clamps both up
	to the children's minimums, so a ratio passed there silently does nothing. Stretch factors are
	what survives a resize. """

	left, right = QLabel("a"), QLabel("b")
	splitter = make_splitter(Qt.Orientation.Horizontal, left, right, stretch=[3, 1])
	splitter.resize(800, 100)
	splitter.show()
	qt_app.processEvents()

	sizes = splitter.sizes()

	assert sizes[0] > sizes[1] * 2

	splitter.hide()

def test_the_oscilloscope_panel_is_built_from_splitters(qt_app, log):
	""" The category widget is the reason all of the above exists - a fixed grid cannot be
	rearranged by the person actually using the instrument. """

	from PyQt6.QtWidgets import QSplitter
	from constellation.ui import OwningBridge
	from constellation.instrument_control.oscilloscope.oscilloscope_gui import OscilloscopeWidget
	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	window = ConstellationWindow(log, add_menu=False)
	scope = RigolDS1000Z("DUMMY", log=log, dummy=True)
	widget = OscilloscopeWidget(window, OwningBridge(scope), log)

	for name in ("body_splitter", "upper_splitter", "side_splitter", "channels_splitter"):
		assert isinstance(getattr(widget, name), QSplitter)

	for name in ("acq_box", "trigger_box", "horiz_box", "channels_box"):
		assert isinstance(getattr(widget, name), CollapsiblePanel)
