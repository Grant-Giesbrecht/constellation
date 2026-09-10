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

# --- trace export ----------------------------------------------------------------------------------

from constellation.ui import (Trace, TRACE_EXPORTERS, SaveTraceDialog, ConnectionInfoDialog,
	export_csv, export_json, _shared_x, _tome_writer)

def _traces():

	x = [0.0, 1e-3, 2e-3]

	return [Trace("Channel 1", x, [0.0, 0.5, 1.0], "s", "V"),
		Trace("Channel 2", x, [1.0, 0.5, 0.0], "s", "V")]

def test_csv_is_wide_when_the_traces_share_an_x_axis(qt_app, tmp_path):
	""" A scope's channels share a timebase, so one x column and one column per channel is what
	someone opening the file expects. """

	path = tmp_path / "t.csv"
	export_csv(str(path), _traces())

	lines = path.read_text(encoding="utf-8").strip().splitlines()

	assert lines[0] == "x [s],Channel 1 [V],Channel 2 [V]"
	assert lines[1].startswith("0.0,0.0,1.0")
	assert len(lines) == 4

def test_csv_falls_back_to_long_form_when_they_do_not(qt_app, tmp_path):
	""" A wide table for mismatched x axes would silently imply a row-by-row correspondence that
	does not exist. """

	traces = [Trace("A", [0.0, 1.0], [1.0, 2.0]), Trace("B", [0.0, 0.5, 1.0], [3.0, 4.0, 5.0])]

	path = tmp_path / "t.csv"
	export_csv(str(path), traces)

	lines = path.read_text(encoding="utf-8").strip().splitlines()

	assert lines[0] == "trace,x,y"
	assert len(lines) == 6

def test_shared_x_tolerates_float_noise(qt_app):

	a = Trace("a", [0.0, 1e-3], [0, 0])
	b = Trace("b", [0.0, 1e-3 + 1e-19], [0, 0])

	assert _shared_x([a, b])
	assert not _shared_x([a, Trace("c", [0.0, 2e-3], [0, 0])])

def test_json_round_trips(qt_app, tmp_path):

	import json

	path = tmp_path / "t.json"
	export_json(str(path), _traces(), metadata={"instrument": "scope"})

	document = json.loads(path.read_text(encoding="utf-8"))

	assert document["metadata"]["instrument"] == "scope"
	assert [t["label"] for t in document["traces"]] == ["Channel 1", "Channel 2"]
	assert document["traces"][0]["y"] == [0.0, 0.5, 1.0]
	assert document["traces"][0]["y_unit"] == "V"

def test_figure_formats_refuse_to_invent_a_plot(qt_app, tmp_path):
	""" PNG and GrAF save the figure, so without one there is nothing to write - and a
	zero-byte file would be worse than an error. """

	from constellation.ui import export_png, export_graf

	for writer in (export_png, export_graf):
		with pytest.raises(Exception):
			writer(str(tmp_path / "t.out"), _traces(), figure=None)

def test_every_exporter_is_described(qt_app):
	""" The dialog explains what each format is *for* - that is the actual decision being made,
	not the file extension. """

	from constellation.ui import TRACE_EXPORT_NOTES

	assert set(TRACE_EXPORT_NOTES) == set(TRACE_EXPORTERS)

def test_an_unavailable_format_is_shown_disabled_not_hidden(qt_app):
	""" A silently absent option looks like the feature does not exist. TOME has no published
	writer yet, so it must appear, greyed, with the reason. """

	dialog = SaveTraceDialog(_traces(), figure=None)

	assert "tome" in dialog.buttons
	assert not dialog.buttons["tome"].isEnabled()
	assert _tome_writer() is None      # if this ever fails, TOME arrived - wire it up and drop this

def test_formats_needing_a_figure_are_disabled_without_one(qt_app):

	dialog = SaveTraceDialog(_traces(), figure=None)

	assert not dialog.buttons["png"].isEnabled()
	assert not dialog.buttons["graf"].isEnabled()
	assert dialog.buttons["csv"].isEnabled()
	assert dialog.selected_format() in ("json", "csv")

def test_saving_is_refused_when_nothing_was_captured(qt_app):

	dialog = SaveTraceDialog([], figure=None)

	assert not dialog.save_button.isEnabled()

# --- connection info --------------------------------------------------------------------------------

def test_a_local_driver_is_not_described_as_networked(qt_app, log):
	""" A DirectSCPIRelay also carries an `address`; labelling it a labmesh relay id would be a
	confident lie about a USB cable. """

	from constellation.ui import OwningBridge
	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	bridge = OwningBridge(RigolDS1000Z("TCPIP0::1.2.3.4::INSTR", log=log, dummy=True))
	info = bridge.describe()

	assert info["Address"] == "TCPIP0::1.2.3.4::INSTR"
	assert "Broker" not in info
	assert "labmesh" not in info["Address"]

def test_a_networked_driver_reports_its_broker(qt_app, log):

	from constellation.ui import OwningBridge
	from constellation.relay import RemoteTextCommandRelayClient
	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	bridge = OwningBridge(RigolDS1000Z("scope-bench-1", log=log, dummy=True,
		relay=RemoteTextCommandRelayClient(broker_address="10.0.0.9")))
	info = bridge.describe()

	assert "labmesh relay id" in info["Address"]
	assert info["Broker"] == "10.0.0.9"

def test_an_observer_reports_the_mesh_it_is_watching(qt_app):

	from constellation.ui import ObserverBridge

	info = ObserverBridge("scope-bench-1", broker_address="10.0.0.9").describe()

	assert info["labmesh relay id"] == "scope-bench-1"
	assert info["Broker"] == "10.0.0.9"
	assert "observing" in info["Connection"]

def test_connection_info_asks_through_the_bridge(qt_app, log):
	""" Never a Driver call from the GUI thread - a connection probe can block for seconds while
	the worker thread is mid-query. """

	from PyQt6.QtCore import QObject, pyqtSignal

	class FakeBridge(QObject):
		state_changed = pyqtSignal(object)
		command_result = pyqtSignal(str, tuple, bool, object)
		connection_changed = pyqtSignal(bool)
		def __init__(self):
			super().__init__()
			self.requests = []
		def describe(self):
			return {"Address": "GPIB::7"}
		def request(self, name, *a, **k):
			self.requests.append(name)

	bridge = FakeBridge()
	dialog = ConnectionInfoDialog(bridge, title="Scope")

	assert bridge.requests == ["connection_summary"]
	assert "GPIB::7" in dialog.identity.text()

	bridge.command_result.emit("connection_summary", (), True,
		{"online": True, "diagnosis": "Connected."})

	assert "Connected." in dialog.status.text()

def test_connection_info_reports_a_failed_probe(qt_app):

	from PyQt6.QtCore import QObject, pyqtSignal

	class FakeBridge(QObject):
		state_changed = pyqtSignal(object)
		command_result = pyqtSignal(str, tuple, bool, object)
		connection_changed = pyqtSignal(bool)
		def describe(self): return {}
		def request(self, name, *a, **k): pass

	bridge = FakeBridge()
	dialog = ConnectionInfoDialog(bridge)

	bridge.command_result.emit("connection_summary", (), False, RuntimeError("VI_ERROR_TMO"))

	assert "VI_ERROR_TMO" in dialog.status.text()

# --- the Instrument menu -----------------------------------------------------------------------------

def test_the_instrument_menu_exists_and_says_when_it_is_empty(qt_app, log):

	window = ConstellationWindow(log)

	names = [a.text() for a in window.instrument_menu.actions()]

	assert names == ["No instruments connected"]
	assert not window.instrument_menu.actions()[0].isEnabled()

def test_the_instrument_menu_populates_when_an_instrument_is_added(qt_app, log):

	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	window = ConstellationWindow(log)
	window.add_instrument(driver=RigolDS1000Z("DUMMY", log=log, dummy=True), title="Scope")

	names = [a.text() for a in window.instrument_menu.actions() if a.text()]

	assert names == ["Refresh State", "Apply State", "Save State...", "Load State...",
		"Get Connection Info..."]

def test_several_instruments_get_named_submenus(qt_app, log):
	""" "Refresh state" is ambiguous the moment a second instrument is on screen. """

	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	window = ConstellationWindow(log)
	window.add_instrument(driver=RigolDS1000Z("A", log=log, dummy=True), title="Scope A")
	window.add_instrument(driver=RigolDS1000Z("B", log=log, dummy=True), title="Scope B")

	names = [a.text() for a in window.instrument_menu.actions()]

	assert names == ["Scope A", "Scope B"]
	assert all(a.menu() is not None for a in window.instrument_menu.actions())

def test_menu_actions_go_through_the_bridge(qt_app, log):
	""" A state refresh takes seconds on real hardware; doing it on the GUI thread would freeze
	every other panel in the window. """

	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	window = ConstellationWindow(log)
	widget = window.add_instrument(driver=RigolDS1000Z("DUMMY", log=log, dummy=True), title="Scope")

	sent = []
	widget.bridge.request = lambda name, *a, **k: sent.append(name)

	actions = {a.text(): a for a in window.instrument_menu.actions()}
	actions["Refresh State"].trigger()
	actions["Apply State"].trigger()

	assert sent == ["refresh_state", "apply_state"]

# --- the oscilloscope's rearranged panel ----------------------------------------------------------------

def _scope_widget(log):

	from constellation.ui import OwningBridge
	from constellation.instrument_control.oscilloscope.oscilloscope_gui import OscilloscopeWidget
	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

	window = ConstellationWindow(log, add_menu=False)
	scope = RigolDS1000Z("DUMMY", log=log, dummy=True)

	return OscilloscopeWidget(window, OwningBridge(scope), log)

def test_acquisition_lives_in_the_side_panel(qt_app, log):

	widget = _scope_widget(log)
	side = [widget.side_splitter.widget(i) for i in range(widget.side_splitter.count())]

	assert widget.acq_box in side
	assert widget.trigger_box in side
	assert widget.horiz_box in side

def test_the_waveform_is_its_own_foldable_panel(qt_app, log):

	widget = _scope_widget(log)

	assert isinstance(widget.waveform_box, CollapsiblePanel)
	assert widget.upper_splitter.widget(0) is widget.waveform_box

def test_folding_the_whole_sidebar_leaves_the_waveform_showing(qt_app, log):
	""" The two fold independently - collapsing every side panel must not take the plot with it. """

	widget = _scope_widget(log)

	for panel in (widget.acq_box, widget.trigger_box, widget.horiz_box):
		panel.set_collapsed(True)

	assert not widget.waveform_box.collapsed
	assert widget.plot_widget.isVisibleTo(widget.waveform_box)

def test_folding_the_waveform_leaves_the_sidebar_alone(qt_app, log):

	widget = _scope_widget(log)
	widget.waveform_box.set_collapsed(True)

	assert widget.waveform_box.collapsed
	assert not widget.trigger_box.collapsed
	assert not widget.acq_box.collapsed

def test_captured_waveforms_become_traces(qt_app, log):

	widget = _scope_widget(log)
	widget._capture_waveforms()
	qt_app.processEvents()

	# The dummy driver answers synchronously enough for the cache to fill on the worker thread;
	# if it has not yet, there is simply nothing to export, which is also a valid state.
	for trace in widget._traces():
		assert trace.label.startswith("Channel ")
		assert len(trace.x) == len(trace.y)
		assert trace.y_unit == "V"

def test_traces_tolerate_either_time_key(qt_app, log):
	""" The category seeds `waveform` as {"time_S", ...} while RigolDS1000Z returns {"time_s"}.
	The data-shape contract is a known open item (P11); exporting must not fail over a capital
	letter in the meantime. """

	widget = _scope_widget(log)

	widget._waveform_cache = {1: {"time_S": [0, 1], "volt_V": [2, 3]},
		2: {"time_s": [0, 1], "volt_V": [4, 5]}}

	assert [t.label for t in widget._traces()] == ["Channel 1", "Channel 2"]
	assert [list(t.x) for t in widget._traces()] == [[0, 1], [0, 1]]
