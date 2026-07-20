from constellation.base import *
import sys
import time
import threading
import queue
import asyncio
from PyQt6 import QtCore, QtGui
from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QMainWindow, QGridLayout, QHBoxLayout, QVBoxLayout, QPushButton,
	QSlider, QGroupBox, QWidget, QTabWidget, QDockWidget, QLabel, QLineEdit, QComboBox)
from PyQt6.QtGui import QAction

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT

from jarnsaxa import from_serial_dict
from labmesh import DirectorClientAgent

# ============================================================================
# InstrumentBridge - decouples the Qt GUI thread from instrument I/O.
#
# See docs/gui_architecture_proposal.md for the full design rationale. The short version: every
# InstrumentWidget is handed an already-running bridge by ConstellationWindow.add_instrument() -
# widget authors never construct a bridge, never touch threading/asyncio, and never touch
# `driver` directly. From a widget author's side this is plain single-threaded Qt: read the
# reconstructed InstrumentState objects that arrive via `state_changed`, and call
# `bridge.request(method_name, *args)` to issue commands.
# ============================================================================

class InstrumentBridge(QObject):
	''' Base class. Owns all interaction with an instrument on a dedicated background thread (or
	asyncio task, for the labmesh-observing subclass) so the Qt GUI thread never blocks on
	instrument communication - one instrument's slow/hung I/O can't freeze any other panel.

	Two concrete subclasses, same signal interface, so a widget never knows or cares which one
	it's connected to:
	  - OwningBridge: owns a real Driver (local or networked CommandRelay - irrelevant here, same
	    as it's irrelevant to Driver itself).
	  - ObserverBridge: owns no Driver at all, just watches a DriverStateBroadcaster's labmesh feed.

	Do not subclass this directly to build a category widget - subclass InstrumentWidget instead
	and receive a bridge, already running, as a constructor argument.
	'''

	# Emitted with a freshly reconstructed InstrumentState object (never a live reference into a
	# Driver's own self.state - see OwningBridge._poll_and_emit for why that matters) each time a
	# new confirmed state snapshot is available, from a poll or a labmesh broadcast.
	state_changed = pyqtSignal(object)

	# Emitted once a requested command finishes: (method_name, args, success, result_or_exception).
	command_result = pyqtSignal(str, tuple, bool, object)

	# Emitted when online/offline status changes.
	connection_changed = pyqtSignal(bool)

	def __init__(self):
		super().__init__()

	def start(self):
		''' Starts the bridge's background thread. Called exactly once, by
		ConstellationWindow.add_instrument() - never by widget code. '''
		raise NotImplementedError

	def stop(self):
		''' Stops the bridge's background thread. Called by ConstellationWindow on shutdown. '''
		raise NotImplementedError

	def request(self, method_name:str, *args, **kwargs):
		''' Asks the bridge to call `method_name` on the instrument (e.g.
		bridge.request("set_div_volt", 1, 2.0)) with the given arguments. Returns immediately -
		the outcome arrives later via command_result. Takes a method NAME (not a bound method)
		specifically so this call looks identical regardless of which bridge subclass is behind
		it - an OwningBridge has a real Driver to look the method up on, an ObserverBridge doesn't.
		'''
		raise NotImplementedError

class OwningBridge(InstrumentBridge):
	''' Owns a real Driver - local (DirectSCPIRelay) or networked (RemoteTextCommandRelayClient),
	the bridge doesn't care which, same as Driver itself doesn't. All interaction happens on one
	dedicated worker thread: queued commands (from request()) are given priority, and the
	instrument's settings are polled periodically via Driver.poll() (already exists - already does
	refresh_state() + state_to_dict()) whenever the queue is idle.

	poll() only refreshes settings/state, not measurement data (get_waveform() and friends can
	legitimately take many seconds - see the RigolDS1000Z waveform-capture work - so those must be
	requested explicitly via request(), never folded into the automatic poll cycle).
	'''

	def __init__(self, driver:Driver, poll_interval_s:float=2.0):
		super().__init__()

		self.driver = driver
		self.poll_interval_s = poll_interval_s

		self._queue = queue.Queue()
		self._thread = None
		self._stop_event = threading.Event()

	def start(self):
		if self._thread is not None:
			return
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()

	def stop(self):
		self._stop_event.set()

	def request(self, method_name:str, *args, **kwargs):
		self._queue.put((method_name, args, kwargs))

	def _run(self):

		last_poll = 0.0

		while not self._stop_event.is_set():

			try:
				method_name, args, kwargs = self._queue.get(timeout=0.1)
			except queue.Empty:
				method_name = None

			if method_name is not None:
				self._execute(method_name, args, kwargs)
				last_poll = time.time()

			elif time.time() - last_poll >= self.poll_interval_s:
				self._poll_and_emit()
				last_poll = time.time()

	def _execute(self, method_name:str, args:tuple, kwargs:dict):

		try:
			method = getattr(self.driver, method_name)
			result = method(*args, **kwargs)
			self.command_result.emit(method_name, args, True, result)
		except Exception as e:
			self.command_result.emit(method_name, args, False, e)

		# The command may have changed instrument state - refresh and push it right away rather
		# than waiting for the next scheduled poll.
		self._poll_and_emit()

	def _poll_and_emit(self):

		try:
			state_dict = self.driver.poll()
			self.connection_changed.emit(self.driver.online)
			# Reconstruct a fresh, independent InstrumentState object rather than emitting
			# self.driver.state directly - Qt signals pass Python object references across
			# threads, and self.driver.state keeps getting mutated in place by this same worker
			# thread on every subsequent poll, which would otherwise be a data race against
			# whatever the GUI thread is doing with the object it received last time.
			self.state_changed.emit(from_serial_dict(state_dict))
		except Exception as e:
			self.connection_changed.emit(False)

class ObserverBridge(InstrumentBridge):
	''' Owns no Driver at all - watches a DriverStateBroadcaster's labmesh feed for `relay_id`, so
	a GUI can show an instrument that some other process (an automation script, or another GUI)
	already owns, without opening a second connection to the instrument itself. See
	docs/labmesh_migration_plan.md for the underlying broker/relay/client model.

	request() is still available (concurrent writers to one instrument are allowed for now - see
	docs/labmesh_migration_plan.md's open questions), issuing the call as a labmesh RPC directly
	against relay_id rather than through a local Driver.
	'''

	def __init__(self, relay_id:str, broker_address:str="127.0.0.1", broker_rpc:str="tcp://BROKER:5750", broker_xpub:str="tcp://BROKER:5752"):
		super().__init__()

		self.relay_id = relay_id
		self.broker_address = broker_address
		self.broker_rpc = broker_rpc
		self.broker_xpub = broker_xpub

		self._thread = None
		self._loop = None
		self._relay_client = None

	def start(self):
		if self._thread is not None:
			return
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()

	def stop(self):
		if self._loop is not None:
			self._loop.call_soon_threadsafe(self._loop.stop)

	def request(self, method_name:str, *args, **kwargs):

		if self._loop is None:
			self.command_result.emit(method_name, args, False, RuntimeError("ObserverBridge not connected yet"))
			return

		asyncio.run_coroutine_threadsafe(self._do_request(method_name, args, kwargs), self._loop)

	async def _do_request(self, method_name:str, args:tuple, kwargs:dict):

		if self._relay_client is None:
			self.command_result.emit(method_name, args, False, RuntimeError(f"relay_id >{self.relay_id}< not found"))
			return

		try:
			params = kwargs if kwargs else list(args)
			result = await self._relay_client.call(method_name, params)
			self.command_result.emit(method_name, args, True, result)
		except Exception as e:
			self.command_result.emit(method_name, args, False, e)

	def _run(self):

		loop = asyncio.new_event_loop()
		asyncio.set_event_loop(loop)
		self._loop = loop
		try:
			loop.run_until_complete(self._main())
		except Exception:
			pass
		finally:
			loop.close()

	async def _main(self):

		client = DirectorClientAgent(broker_address=self.broker_address, broker_rpc=self.broker_rpc, broker_xpub=self.broker_xpub)
		await client.connect()

		def _on_state(rid, state):
			if rid != self.relay_id:
				return
			self.connection_changed.emit(True)
			self.state_changed.emit(from_serial_dict(state))

		client.on_state(_on_state)

		try:
			self._relay_client = await client.get_relay_agent(self.relay_id)
		except Exception:
			self.connection_changed.emit(False)

		while True:
			await asyncio.sleep(1)

# ============================================================================
# Indicator convention shared by every Tracked* control.
# ============================================================================

STATUS_COLORS = {
	"confirmed": "#2ecc71",  # green  - matches the last requested value
	"pending":   "#f39c12",  # amber  - request sent, awaiting confirmation
	"mismatch":  "#e74c3c",  # red    - confirmed value differs from what was requested
	"stale":     "#888888",  # grey   - no update recently; bridge offline or instrument unresponsive
}

def _dot_style(color:str) -> str:
	return f"background-color:{color}; border-radius:5px; min-width:10px; min-height:10px; max-width:10px; max-height:10px;"

class IndicatorButton(QWidget):
	''' A checkable button with two small status lights beside it: one showing the current
	setpoint (what was last requested - green=ON, dark=OFF, grey=nothing requested yet), one
	showing pending/mismatch/stale/confirmed status (see STATUS_COLORS).

	Pure visual component - knows nothing about bridges or drivers. Behaves like a checkable
	QPushButton for the purposes a widget author needs (isChecked/setChecked/setText/toggled).
	TrackedToggle is what actually wires one of these to a bridge.
	'''

	toggled = pyqtSignal(bool)

	def __init__(self, text:str="", parent=None):
		super().__init__(parent)

		self._button = QPushButton(text)
		self._button.setCheckable(True)
		self._button.toggled.connect(self.toggled)

		self._setpoint_light = QLabel()
		self._status_light = QLabel()
		for light in (self._setpoint_light, self._status_light):
			light.setFixedSize(10, 10)

		lights = QVBoxLayout()
		lights.setSpacing(2)
		lights.addWidget(self._setpoint_light)
		lights.addWidget(self._status_light)

		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.addWidget(self._button)
		layout.addLayout(lights)
		self.setLayout(layout)

		self.set_setpoint_indicator(None)
		self.set_status("stale")

	def isChecked(self) -> bool:
		return self._button.isChecked()

	def setChecked(self, value:bool):
		self._button.setChecked(value)

	def setText(self, text:str):
		self._button.setText(text)

	def blockSignals(self, block:bool):
		self._button.blockSignals(block)
		return super().blockSignals(block)

	def set_setpoint_indicator(self, on):
		''' `on`: True/False for the last-requested value, or None if nothing requested yet. '''
		color = "#888888" if on is None else ("#2ecc71" if on else "#555555")
		self._setpoint_light.setStyleSheet(_dot_style(color))
		self._setpoint_light.setToolTip("setpoint: " + ("unknown" if on is None else ("ON" if on else "OFF")))

	def set_status(self, status:str):
		self._status_light.setStyleSheet(_dot_style(STATUS_COLORS.get(status, "#888888")))
		self._status_light.setToolTip(f"status: {status}")

# ============================================================================
# Tracked* controls - the reusable setpoint-vs-actual building blocks. See
# docs/gui_authoring_guide.md for how a category widget author is expected to use these.
# ============================================================================

class _TrackedControlBase(QWidget):
	''' Shared setpoint/pending/mismatch/stale state machine used by every Tracked* control. Not
	instantiated directly - see TrackedToggle/TrackedValue/TrackedChoice. '''

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str, set_args:callable=None, stale_after_s:float=5.0):
		super().__init__()

		self.bridge = bridge
		self.get = get
		self.set_method = set_method
		self.set_args = set_args if set_args is not None else (lambda v: (v,))
		self.stale_after_s = stale_after_s

		self._setpoint = None    # last value the user requested - None until they request one
		self._confirmed = None   # last confirmed value seen in a state_changed update
		self._pending = False    # True while a request is in flight
		self._last_update = 0.0  # time.time() of the last relevant state_changed/command_result

		bridge.state_changed.connect(self._on_state_changed)
		bridge.command_result.connect(self._on_command_result)
		bridge.connection_changed.connect(self._on_connection_changed)

		self._stale_timer = QTimer(self)
		self._stale_timer.timeout.connect(self._check_stale)
		self._stale_timer.start(1000)

	# --- implemented by subclasses ---

	def _display(self, confirmed_value, setpoint_value, status:str):
		raise NotImplementedError

	# --- called by subclasses when the user interacts with the control ---

	def _user_changed(self, new_value):
		self._setpoint = new_value
		self._pending = True
		self._refresh_display()
		self.bridge.request(self.set_method, *self.set_args(new_value))

	# --- internal plumbing ---

	def _on_state_changed(self, state):
		try:
			value = self.get(state)
		except Exception:
			return  # this field isn't present in this update (e.g. a different instrument)
		self._confirmed = value
		self._pending = False
		self._last_update = time.time()
		if self._setpoint is None:
			self._setpoint = value  # nothing requested yet - setpoint mirrors reality
		self._refresh_display()

	def _on_command_result(self, method_name, args, success, result):
		if method_name != self.set_method:
			return
		self._last_update = time.time()
		if not success:
			self._pending = False
			self._refresh_display()

	def _on_connection_changed(self, online):
		if not online:
			self._refresh_display()

	def _check_stale(self):
		if self._last_update and (time.time() - self._last_update) > self.stale_after_s:
			self._refresh_display()

	def _status(self) -> str:
		# Pending takes priority over staleness: a freshly-built control (constructed lazily
		# inside a state_changed handler, e.g. OscilloscopeWidget._build_channels) never actually
		# receives the very state_changed event that triggered its own creation, so it starts
		# with _last_update == 0 - if the user interacts with it before the *next* update arrives,
		# it should read as "pending", not "stale" (which should mean "haven't heard from the
		# bridge in a while", not "haven't heard from it yet").
		if self._pending:
			return "pending"
		if self._last_update == 0 or (time.time() - self._last_update) > self.stale_after_s:
			return "stale"
		if self._setpoint is not None and self._confirmed is not None and self._setpoint != self._confirmed:
			return "mismatch"
		return "confirmed"

	def _refresh_display(self):
		self._display(self._confirmed, self._setpoint, self._status())

class TrackedToggle(_TrackedControlBase):
	''' A checkable on/off control wired to a bridge. Example:

		TrackedToggle(bridge, "Enable", get=lambda s: s.channels[1].chan_en, set_method="set_chan_enable", set_args=lambda v: (1, v))
	'''

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str, set_args:callable=None, stale_after_s:float=5.0):
		super().__init__(bridge, label, get, set_method, set_args, stale_after_s)

		self.button = IndicatorButton(label)
		self.button.toggled.connect(self._on_toggled)

		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.addWidget(self.button)
		self.setLayout(layout)

	def _on_toggled(self, checked):
		# Also fires for the programmatic setChecked() calls _display() makes - only treat this
		# as a user action if it actually changes the setpoint, so refreshing the display can't
		# loop back into issuing a redundant request.
		if checked == self._setpoint:
			return
		self._user_changed(checked)

	def _display(self, confirmed_value, setpoint_value, status):
		shown = setpoint_value if setpoint_value is not None else confirmed_value
		self.button.setChecked(bool(shown) if shown is not None else False)
		self.button.set_setpoint_indicator(setpoint_value)
		self.button.set_status(status)

class TrackedValue(_TrackedControlBase):
	''' A numeric/text entry control wired to a bridge. Example:

		TrackedValue(bridge, "Volts/div", get=lambda s: s.channels[1].div_volt, set_method="set_div_volt", set_args=lambda v: (1, v), unit="V")
	'''

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str, set_args:callable=None, validator=None, unit:str="", stale_after_s:float=5.0):
		super().__init__(bridge, label, get, set_method, set_args, stale_after_s)

		self.edit = QLineEdit()
		if validator is not None:
			self.edit.setValidator(validator)
		self.edit.editingFinished.connect(self._on_edited)

		self._setpoint_light = QLabel()
		self._status_light = QLabel()
		for light in (self._setpoint_light, self._status_light):
			light.setFixedSize(10, 10)
		lights = QVBoxLayout()
		lights.setSpacing(2)
		lights.addWidget(self._setpoint_light)
		lights.addWidget(self._status_light)

		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.addWidget(self.edit)
		if unit:
			layout.addWidget(QLabel(unit))
		layout.addLayout(lights)
		self.setLayout(layout)

	def _on_edited(self):
		text = self.edit.text()
		try:
			value = float(text)
		except ValueError:
			self._refresh_display()  # revert to last known-good display
			return
		if value == self._setpoint:
			return
		self._user_changed(value)

	def _display(self, confirmed_value, setpoint_value, status):

		shown = setpoint_value if setpoint_value is not None else confirmed_value
		if shown is not None and not self.edit.hasFocus():
			self.edit.setText(str(shown))

		self._setpoint_light.setStyleSheet(_dot_style("#2ecc71" if setpoint_value is not None else "#888888"))
		self._status_light.setStyleSheet(_dot_style(STATUS_COLORS.get(status, "#888888")))
		self._status_light.setToolTip(f"status: {status} (setpoint={setpoint_value}, confirmed={confirmed_value})")

class TrackedChoice(_TrackedControlBase):
	''' A dropdown/enum control wired to a bridge. Example:

		TrackedChoice(bridge, "Coupling", get=lambda s: s.channels[1].coupling, set_method="set_coupling",
			set_args=lambda v: (1, v), choices=[Oscilloscope.COUPLING_AC, Oscilloscope.COUPLING_DC, Oscilloscope.COUPLING_GND])
	'''

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str, choices:list, set_args:callable=None, stale_after_s:float=5.0):
		super().__init__(bridge, label, get, set_method, set_args, stale_after_s)

		self._choices = list(choices)

		self.combo = QComboBox()
		self.combo.addItems([str(c) for c in self._choices])
		self.combo.activated.connect(self._on_activated)

		self._setpoint_light = QLabel()
		self._status_light = QLabel()
		for light in (self._setpoint_light, self._status_light):
			light.setFixedSize(10, 10)
		lights = QHBoxLayout()
		lights.setSpacing(2)
		lights.addWidget(self._setpoint_light)
		lights.addWidget(self._status_light)

		layout = QHBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.addWidget(self.combo)
		layout.addLayout(lights)
		self.setLayout(layout)

	def _on_activated(self, index:int):
		value = self._choices[index]
		if value == self._setpoint:
			return
		self._user_changed(value)

	def _display(self, confirmed_value, setpoint_value, status):

		shown = setpoint_value if setpoint_value is not None else confirmed_value
		if shown is not None and shown in self._choices:
			idx = self._choices.index(shown)
			if self.combo.currentIndex() != idx:
				self.combo.blockSignals(True)
				self.combo.setCurrentIndex(idx)
				self.combo.blockSignals(False)

		self._setpoint_light.setStyleSheet(_dot_style("#2ecc71" if setpoint_value is not None else "#888888"))
		self._status_light.setStyleSheet(_dot_style(STATUS_COLORS.get(status, "#888888")))
		self._status_light.setToolTip(f"status: {status} (setpoint={setpoint_value}, confirmed={confirmed_value})")

# ============================================================================
# Category -> widget registration, so ConstellationWindow.add_instrument(driver) works without
# the caller needing to know which widget class handles that driver's category.
# ============================================================================

_GUI_REGISTRY = {}

def register_gui(category_cls):
	''' Class decorator: registers an InstrumentWidget subclass as the GUI for every driver in
	`category_cls` (e.g. @register_gui(Oscilloscope) - keyed by category, not by specific driver
	model, so any current or future driver in that category gets a working GUI for free. '''
	def _decorator(widget_cls):
		_GUI_REGISTRY[category_cls] = widget_cls
		return widget_cls
	return _decorator

def _find_registered_category(driver_cls):
	for cls in driver_cls.__mro__:
		if cls in _GUI_REGISTRY:
			return cls
	return None

# ============================================================================
# Window and widget base classes
# ============================================================================

class ConstellationWindow(QMainWindow):

	def __init__(self, log:plf.LogPile, add_menu:bool=True):
		super().__init__()
		self.log = log

		self.instrument_widgets = []
		self._bridges = []

		self.setDockNestingEnabled(True)

		if add_menu:
			self.add_basic_menu_bar()

	def add_instrument(self, driver:Driver=None, *, relay_id:str=None, broker_address:str="127.0.0.1",
			broker_rpc:str="tcp://BROKER:5750", broker_xpub:str="tcp://BROKER:5752",
			category=None, title:str=None, dock_area=Qt.DockWidgetArea.TopDockWidgetArea):
		''' Builds the right bridge and registered widget for an instrument and docks the
		resulting panel into this window.

		Pass exactly one of:
		  driver:   a Driver instance this window should own and drive directly (local or
		            networked CommandRelay, doesn't matter) - uses an OwningBridge.
		  relay_id: a labmesh relay_id to observe (some other process already owns the Driver,
		            e.g. an automation script) - uses an ObserverBridge, and `category` must be
		            given explicitly since there's no local driver to infer it from.
		'''

		if (driver is None) == (relay_id is None):
			raise ValueError("add_instrument() needs exactly one of driver= or relay_id=")

		if driver is not None:
			bridge = OwningBridge(driver)
			resolved_category = category or _find_registered_category(type(driver))
			panel_title = title or driver.id.short_str()
		else:
			if category is None:
				raise ValueError("add_instrument(relay_id=...) needs an explicit category= (no local driver to infer it from)")
			bridge = ObserverBridge(relay_id, broker_address=broker_address, broker_rpc=broker_rpc, broker_xpub=broker_xpub)
			resolved_category = category
			panel_title = title or relay_id

		widget_cls = _GUI_REGISTRY.get(resolved_category)
		if widget_cls is None:
			raise LookupError(f"No GUI registered for category >{resolved_category}< - use @register_gui(...) on a widget class.")

		widget = widget_cls(self, bridge, self.log)
		bridge.start()
		self._bridges.append(bridge)

		dock = QDockWidget(panel_title, self)
		dock.setWidget(widget)
		dock.setFeatures(
			QDockWidget.DockWidgetFeature.DockWidgetMovable
			| QDockWidget.DockWidgetFeature.DockWidgetFloatable
			| QDockWidget.DockWidgetFeature.DockWidgetClosable
		)
		self.addDockWidget(dock_area, dock)

		return widget

	def closeEvent(self, event):
		for bridge in self._bridges:
			bridge.stop()
		super().closeEvent(event)

	def add_basic_menu_bar(self):

		self.bar = self.menuBar()

		#----------------- File Menu ----------------

		self.file_menu = self.bar.addMenu("File")

		self.close_window_act = QAction("Close Window", self)
		self.close_window_act.setShortcut("Ctrl+W")
		self.close_window_act.triggered.connect(self._basic_menu_close)
		self.file_menu.addAction(self.close_window_act)

		self.view_log_act = QAction("View Log", self)
		self.view_log_act.setShortcut("Shift+L")
		self.view_log_act.triggered.connect(self._basic_menu_view_log)
		self.file_menu.addAction(self.view_log_act)

	def _basic_menu_close(self):
		self.close()
		sys.exit(0)

	def _basic_menu_view_log(self):
		self.log.error(f"Log viewing not implemented.")
		pass

class PlotWidget(QWidget):

	def __init__(self, main_window, log:plf.LogPile, cust_render_func:callable=None, **kwargs): #, xlabel:str="", ylabel:str="", title:str="", ):
		super().__init__(main_window)

		self.main_window = main_window
		self.log = log
		self.custom_render_func = cust_render_func

		# Create figure in matplotlib
		self.fig1 = plt.figure()
		self.gs = self.fig1.add_gridspec(1, 1)
		self.ax1a = self.fig1.add_subplot(self.gs[0, 0])

		# Create Qt Figure Canvas
		self.fig_canvas = FigureCanvas(self.fig1)
		self.fig_toolbar = NavigationToolbar2QT(self.fig_canvas, self)

		self.grid = QGridLayout()
		self.grid.addWidget(self.fig_toolbar, 0, 0)
		self.grid.addWidget(self.fig_canvas, 1, 0)

		self.setLayout(self.grid)

		self._render_widget()

	def _render_widget(self):

		# Call custom renderer if provided
		if self.custom_render_func is not None:
			self.custom_render_func(self)

		self.fig1.tight_layout()
		self.fig1.canvas.draw_idle()

		self.is_current = True

class InstrumentWidget(QWidget):
	''' Base class for a category's front-panel-style GUI widget. Subclasses receive an
	already-running `bridge` (built by ConstellationWindow.add_instrument()) and build their
	layout using TrackedToggle/TrackedValue/TrackedChoice wired to it.

	Hard rule: never touch `bridge.driver` (or any Driver at all) directly from widget code - only
	go through `bridge.request(...)` and the two hooks below / Tracked* controls' own wiring. This
	is what keeps the GUI thread from ever blocking on instrument I/O, and avoids racing the
	bridge's own worker thread over the driver's state.
	'''

	def __init__(self, main_window, bridge:InstrumentBridge, log:plf.LogPile):
		super().__init__(main_window)

		self.main_window = main_window
		self.log = log
		self.bridge = bridge

		self.main_layout = QGridLayout()

		bridge.state_changed.connect(self.on_state_changed)
		bridge.connection_changed.connect(self.on_connection_changed)

		self.main_window.instrument_widgets.append(self)

	def on_state_changed(self, state):
		''' Optional hook for anything a widget needs beyond what its Tracked* controls already
		handle automatically (e.g. redrawing a waveform plot). Default no-op - most of a category
		widget's per-field updating should come from Tracked* controls, not this. '''
		pass

	def on_connection_changed(self, online:bool):
		''' Optional hook, e.g. to grey out the whole panel while offline. Default no-op. '''
		pass
