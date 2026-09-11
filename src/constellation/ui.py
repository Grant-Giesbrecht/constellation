from constellation.base import *
import os
import sys
import time
import threading
import queue
import asyncio
from PyQt6 import QtCore, QtGui
from PyQt6.QtCore import Qt, QObject, QTimer, pyqtSignal
from PyQt6.QtWidgets import (QMainWindow, QGridLayout, QHBoxLayout, QVBoxLayout, QPushButton,
	QSlider, QGroupBox, QWidget, QTabWidget, QDockWidget, QLabel, QLineEdit, QComboBox, QDialog,
	QDialogButtonBox, QSizePolicy, QFrame, QCheckBox, QLCDNumber, QApplication, QSplitter,
	QToolButton, QRadioButton, QFileDialog, QMessageBox)
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QDoubleValidator

import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT

# Must match the serializer base.py uses in state_to_dict()/to_serial_dict() - stardust, not the
# legacy jarnsaxa. Using jarnsaxa here silently returns the raw dict (it can't parse stardust's
# __serializer_format__ envelope), so bridge.state_changed would hand widgets a plain dict instead
# of a reconstructed InstrumentState, breaking every category GUI's on_state_changed / Tracked*.
from stardust.serializer import from_serial_dict
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

	# Whether this bridge reads the instrument on a schedule it controls. An ObserverBridge does not
	# - its updates arrive whenever the owning process broadcasts - so it has nothing to configure.
	supports_polling = False

	def __init__(self):
		super().__init__()

		self.poll_enabled = False
		self.poll_interval_s = None

	def settings_key(self):
		''' A stable name for this instrument, under which its panel settings are saved, or None to
		not save them. The same instrument at the same address gets its settings back next time. '''
		return None

	def set_polling(self, enabled:bool, interval_s:float=None):
		''' Turns automatic state polling on or off, and optionally changes its period.

		Raises:
			NotImplementedError: If this bridge does not poll (see supports_polling).
			ValueError: If `interval_s` is not a positive number.
		'''

		if not self.supports_polling:
			raise NotImplementedError(f"{type(self).__name__} does not poll its instrument.")

		if interval_s is not None:
			interval_s = float(interval_s)
			if interval_s <= 0:
				raise ValueError(f"Polling interval must be positive, got {interval_s}.")
			self.poll_interval_s = interval_s

		self.poll_enabled = bool(enabled)

	def start(self):
		''' Starts the bridge's background thread. Called exactly once, by
		ConstellationWindow.add_instrument() - never by widget code. '''
		raise NotImplementedError

	def stop(self):
		''' Stops the bridge's background thread. Called by ConstellationWindow on shutdown. '''
		raise NotImplementedError

	def describe(self) -> dict:
		''' Where this bridge's instrument is, as static addressing information.

		Lives on the bridge rather than being read off a Driver by the caller, for the usual
		reason: an ObserverBridge has no Driver at all, and reading one from the GUI thread races
		the worker thread. Everything returned here is fixed at construction, so it is safe to
		read at any time.

		Returns:
			dict: Human-readable label -> value, for display.
		'''

		return {}

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

	supports_polling = True

	def __init__(self, driver:Driver, poll_interval_s:float=2.0, poll_enabled:bool=True):
		super().__init__()

		self.driver = driver
		self.poll_interval_s = poll_interval_s

		# With polling off, the instrument is only touched by explicit requests - which, from a
		# Parameter* control, means only when the user changes something. See set_polling().
		self.poll_enabled = poll_enabled

		self._queue = queue.Queue()
		self._thread = None
		self._stop_event = threading.Event()

		# {method_name: (command, response)} - the actual SCPI behind the last call to each driver
		# method, for ParameterDetailDialog to show a user what a control really sends. Captured
		# here rather than read live off the relay because the relay only knows the most recent
		# command *globally*, which would attribute one control's SCPI to whichever control asked
		# last. Written on this worker thread and read from the GUI thread; both are whole-object
		# dict operations on a str tuple, so no lock is needed for a display-only value.
		self.last_scpi = {}

	def start(self):
		if self._thread is not None:
			return
		self._thread = threading.Thread(target=self._run, daemon=True)
		self._thread.start()

	def stop(self):
		self._stop_event.set()

	def request(self, method_name:str, *args, **kwargs):
		self._queue.put((method_name, args, kwargs))

	def settings_key(self):
		return f"{type(self.driver).__name__}@{getattr(self.driver, 'address', '') or ''}"

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

			elif self.poll_enabled and time.time() - last_poll >= self.poll_interval_s:
				self._poll_and_emit()
				last_poll = time.time()

	def _execute(self, method_name:str, args:tuple, kwargs:dict):

		try:
			method = getattr(self.driver, method_name)
			result = method(*args, **kwargs)
			self._record_scpi(method_name)
			self.command_result.emit(method_name, args, True, result)
		except Exception as e:
			self._record_scpi(method_name)
			self.command_result.emit(method_name, args, False, e)

		# The command may have changed instrument state, so push it right away rather than waiting
		# for the next scheduled poll. A full refresh reads EVERY parameter back, though, which is
		# exactly the traffic a user turns polling off to avoid - so without polling, emit only what
		# the driver already tracks. The command itself has already read back the one value it set.
		self._poll_and_emit(refresh=self.poll_enabled)

	def describe(self) -> dict:

		driver = self.driver
		relay = getattr(driver, "relay", None)

		info = {
			"Connection": "owned by this process",
			"Driver": type(driver).__name__,
			"Address": getattr(driver, "address", "") or "(none)",
			"Relay": type(relay).__name__ if relay is not None else "(none)",
			"Instrument ID": (getattr(getattr(driver, "id", None), "idn_model", "") or "(not read)"),
		}

		if getattr(driver, "dummy", False):
			info["Connection"] = "dummy - no instrument attached"

		# A networked driver's `address` is a labmesh relay id rather than a VISA resource string,
		# and the broker it resolves through is the other half of the answer to "where is this".
		# Keyed off the broker attributes, which only a RemoteTextCommandRelayClient has - a local
		# relay also carries an `address`, and labelling that one "labmesh relay id" would be a
		# confident lie about a USB cable.
		if hasattr(relay, "broker_address"):

			info["Address"] = f"{info['Address']}  (labmesh relay id)"

			for label, attribute in (("Broker", "broker_address"), ("Broker RPC", "broker_rpc"),
					("Broker XPUB", "broker_xpub")):
				value = getattr(relay, attribute, None)
				if value:
					info[label] = value

		return info

	def _record_scpi(self, method_name:str):
		''' Notes the SCPI the driver just sent, attributed to the method that sent it. '''

		relay = getattr(self.driver, "relay", None)
		if relay is None:
			return

		command = getattr(relay, "last_command", None)
		if command is not None:
			self.last_scpi[method_name] = (command, getattr(relay, "last_response", None))

	def _poll_and_emit(self, refresh:bool=True):
		''' Emits the driver's state; with `refresh`, reads it from the instrument first. '''

		try:
			state_dict = self.driver.poll() if refresh else self.driver.state_to_dict()
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

	def settings_key(self):
		return f"observed@{self.relay_id}"

	def describe(self) -> dict:

		return {
			"Connection": "observing - another process owns this instrument",
			"labmesh relay id": self.relay_id,
			"Broker": self.broker_address,
			"Broker RPC": self.broker_rpc,
			"Broker XPUB": self.broker_xpub,
		}

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
# Window chrome: keyboard shortcuts and collapsible/resizable panels.
# ============================================================================

def _key_bindings(standard, fallback:str) -> list:
	''' Every key sequence for a standard action, with a guaranteed fallback.

	`QKeySequence.keyBindings()` gives the platform-correct answer - Cmd+W on macOS, Ctrl+W
	elsewhere - but it returns *nothing* for Quit on Windows, where quitting is conventionally a
	menu-only action. Constellation wants the shortcut to exist everywhere, so an explicit
	fallback is appended whenever the platform doesn't supply it.
	'''

	sequences = list(QKeySequence.keyBindings(standard))

	explicit = QKeySequence(fallback)
	if not any(seq.matches(explicit) == QKeySequence.SequenceMatch.ExactMatch for seq in sequences):
		sequences.append(explicit)

	return sequences

def install_window_shortcuts(window, on_close=None, on_quit=None) -> list:
	''' Gives a top-level window the two shortcuts every desktop app is expected to have:
	Cmd/Ctrl-W closes this window, Cmd/Ctrl-Q quits the application.

	Call this on any window Constellation puts on screen - the main window, and any dialog that
	can outlive the click that opened it. Scoped to the window, so a dialog's Cmd-W closes the
	dialog and not whatever is behind it.

	Args:
		window (QWidget): The top-level window.
		on_close (callable): Override for Cmd/Ctrl-W. Defaults to `window.close`.
		on_quit (callable): Override for Cmd/Ctrl-Q. Defaults to quitting the application.

	Returns:
		list: The QShortcut objects, parented to `window` (so they die with it).
	'''

	shortcuts = []

	for standard, fallback, slot in (
			(QKeySequence.StandardKey.Close, "Ctrl+W", on_close or window.close),
			(QKeySequence.StandardKey.Quit, "Ctrl+Q", on_quit or QApplication.quit)):

		for sequence in _key_bindings(standard, fallback):
			shortcut = QShortcut(sequence, window)
			shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
			shortcut.activated.connect(slot)
			shortcuts.append(shortcut)

	return shortcuts

_QT_MAX_SIZE = 16777215   # QWIDGETSIZE_MAX - "no maximum", as Qt spells it

class CollapsiblePanel(QWidget):
	''' A titled frame whose contents can be folded away, for use in place of a QGroupBox.

	Two reasons this exists rather than `QGroupBox.setCheckable(True)`: a checkable group box
	*disables* its contents rather than hiding them, so it frees no space at all; and its checkbox
	reads as "this feature is off", which is a completely different claim from "I have folded this
	away". A front panel with six sections is mostly sections you are not using right now.

	Collapsing clamps the widget's maximum height to its header, so a QSplitter holding one gives
	the space back to its neighbours instead of leaving a hole.

	Put the contents in `panel.content` (a plain QWidget - set a layout on it), or hand a layout
	straight to `set_content_layout()`.
	'''

	toggled = pyqtSignal(bool)

	def __init__(self, title:str, parent=None, collapsed:bool=False,
			fold=Qt.Orientation.Vertical):
		super().__init__(parent)

		# Which dimension folding gives back. A panel stacked vertically should surrender its
		# HEIGHT; one sitting in a row of columns (a per-channel strip) should surrender its
		# WIDTH, or collapsing it just leaves an empty column where it was.
		self.fold = fold

		self.header = QToolButton()
		self.header.setText(title)
		self.header.setCheckable(True)
		self.header.setChecked(not collapsed)
		self.header.setAutoRaise(True)
		self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
		self.header.setArrowType(Qt.ArrowType.DownArrow if not collapsed else Qt.ArrowType.RightArrow)
		self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
		self.header.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))
		self.header.setToolTip("Click to fold this panel away")
		self.header.toggled.connect(self._on_toggled)

		self.content = QWidget()

		self.frame = QFrame()
		self.frame.setFrameShape(QFrame.Shape.StyledPanel)

		frame_layout = QVBoxLayout()
		frame_layout.setContentsMargins(4, 2, 4, 4)
		frame_layout.setSpacing(2)
		frame_layout.addWidget(self.header)
		frame_layout.addWidget(self.content)
		self.frame.setLayout(frame_layout)

		layout = QVBoxLayout()
		layout.setContentsMargins(0, 0, 0, 0)
		layout.addWidget(self.frame)
		self.setLayout(layout)

		self._apply_collapsed(collapsed)

	def set_content_layout(self, layout):
		''' Convenience for `panel.content.setLayout(layout)`. '''

		self.content.setLayout(layout)

	@property
	def collapsed(self) -> bool:
		return not self.header.isChecked()

	def set_collapsed(self, collapsed:bool):

		if bool(collapsed) == self.collapsed:
			return

		self.header.setChecked(not collapsed)

	def _on_toggled(self, expanded:bool):

		self._apply_collapsed(not expanded)
		self.toggled.emit(not expanded)

	def _apply_collapsed(self, collapsed:bool):

		self.content.setVisible(not collapsed)
		self.header.setArrowType(Qt.ArrowType.RightArrow if collapsed else Qt.ArrowType.DownArrow)

		# Clamping the maximum is what actually frees the space: hiding the content alone leaves a
		# splitter holding the old size, so the panel collapses into a blank gap rather than
		# giving its room to its neighbours.
		margins = self.frame.layout().contentsMargins()

		if not collapsed:
			self.setMaximumHeight(_QT_MAX_SIZE)
			self.setMaximumWidth(_QT_MAX_SIZE)
		elif self.fold == Qt.Orientation.Vertical:
			self.setMaximumHeight(margins.top() + margins.bottom() + self.header.sizeHint().height() + 4)
		else:
			self.setMaximumWidth(margins.left() + margins.right() + self.header.sizeHint().width() + 8)

		self.updateGeometry()

def make_splitter(orientation, *widgets, stretch=None, sizes=None, collapsible:bool=False) -> QSplitter:
	''' A QSplitter with the handles made visible and children that keep their minimums.

	Qt's default handle is a 1px hairline that nobody discovers, so panels look fixed even when
	they are not. `collapsible=False` (the default) stops a drag from swallowing a child entirely -
	the CollapsiblePanel header is the honest way to fold something away, and a panel dragged to
	zero width just looks broken.

	Args:
		stretch (sequence): Relative shares of the extra space, one per widget. This is what you
			almost always want - it survives a resize.
		sizes (sequence): Initial sizes in PIXELS. `setSizes([3, 1])` does not mean "3:1", it means
			three pixels and one pixel, which Qt then clamps up to the children's minimums - so a
			ratio passed here silently does nothing useful.
	'''

	splitter = QSplitter(orientation)
	splitter.setHandleWidth(6)
	splitter.setChildrenCollapsible(collapsible)

	for widget in widgets:
		splitter.addWidget(widget)

	if stretch:
		for index, factor in enumerate(stretch):
			splitter.setStretchFactor(index, factor)

	if sizes:
		splitter.setSizes(list(sizes))

	return splitter

# ============================================================================
# Parameter controls - the dense set-point / process-variable family.
#
# The Tracked* controls above show one box and one status lamp: the box holds "the setpoint if
# you've set one, otherwise whatever the instrument last said", and the lamp collapses
# pending/mismatch/stale into a single colour. That is fine for a simple panel and wrong for
# diagnosing an instrument, because the two numbers a user needs to compare - what I asked for,
# what the instrument reports - are never on screen at the same time, and the one lamp answers
# three different questions at once.
#
# The Parameter* family below separates them, and then lets you choose how much of that
# separation to show. One set of plumbing, two densities:
#
#   FULL                              COMPACT
#     Parameter Name
#   <|SP [ 0.55  ] V   * * *          Name: [ 0.55 ] V   * *
#     PV|> [ 0.5  ] V
#
# The mode is a constructor argument AND changeable live (click any lamp), so a panel can be built
# dense and expanded in place when something looks wrong - which is exactly when the extra row is
# worth its space.
# ============================================================================

ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")

class ParameterView:
	''' How much of a parameter's state to show.

	FULL:    title, setpoint row, measured row, three lamps (verification, sent, measured).
	COMPACT: inline label, setpoint row, two lamps (sent, measured). The default, and what the
	         category widgets use.

	COMPACT deliberately drops the *verification* lamp rather than one of the runtime lamps.
	Verification is a fixed property of the driver and the records - it cannot change while a
	panel is open, so it is reference material rather than something to monitor. Which of "did my
	command land" and "does the instrument agree" is failing is the live question, and that is
	what a compact panel keeps. Verification is still one lamp-click away in the detail window,
	and a method the hardware cannot do still comes up as a disabled control either way.
	'''

	FULL = "full"
	COMPACT = "compact"

	ORDER = (COMPACT, FULL)

	LABELS = {
		FULL: "Full",
		COMPACT: "Compact",
	}

# Verification: can this driver method be trusted? Sourced from the hardware-verification records
# (see docs/hardware_verification.md), NOT from anything happening at runtime.
VERIFICATION_COLORS = {
	"confirmed":   "#2ecc71",  # green  - a person watched the instrument do this
	"roundtrip":   "#5dade2",  # blue   - set/read-back agreed on real hardware, nobody watched
	"untested":    "#f1c40f",  # yellow - implemented, never checked (or the check has expired)
	"broken":      "#e74c3c",  # red    - checked against hardware and it did not work
	"unavailable": "#4a4a4a",  # dark   - the hardware cannot do this; the control is disabled
	"unknown":     "#888888",  # grey   - no local driver to ask (an ObserverBridge)
}

# Setpoint: did what I asked for reach the instrument?
SEND_COLORS = {
	"sent":    "#2ecc71",  # green  - the driver call returned successfully
	"unsent":  "#f1c40f",  # yellow - nothing requested yet, or a request is in flight
	"failed":  "#e74c3c",  # red    - the driver call failed
}

# Process variable: does the instrument agree with what I asked for?
VALUE_COLORS = {
	"match":       "#2ecc71",  # green  - read back and agrees within tolerance
	"unqueried":   "#f1c40f",  # yellow - setpoint changed, no read-back since
	"mismatch":    "#888888",  # grey   - read back and does NOT agree
	"query_error": "#e74c3c",  # red    - could not read the value at all
}

# `mismatch` is deliberately GREY rather than red. An instrument quantizes: ask a scope for
# 0.55 V/div and it will report 0.5, forever. That is the instrument working correctly, so
# colouring it as an error would leave a panel full of red lamps that everyone learns to ignore -
# and the one that means something would be lost among them. Grey says "these two numbers differ,
# look at them", which is exactly what it means.

# One-line explanations, shown in tooltips and spelled out in the detail dialog.
VERIFICATION_TEXT = {
	"confirmed": "A person watched the instrument and confirmed this actually works.",
	"roundtrip": "Set and read back correctly on real hardware - but nobody watched the front panel, so this only proves the set/get pair agrees with itself.",
	"untested": "Implemented, but never checked against real hardware - or the check has expired because the code or the instrument changed since.",
	"broken": "Checked against real hardware and it did not work.",
	"unavailable": "This instrument cannot do this, or nobody has written it yet. The control is disabled.",
	"unknown": "No local driver to ask - this panel is observing an instrument owned by another process.",
}

SEND_TEXT = {
	"sent": "The driver call returned successfully.",
	"unsent": "Nothing has been requested yet, or a request is still in flight.",
	"failed": "The driver call failed.",
}

VALUE_TEXT = {
	"match": "The instrument's value agrees with the setpoint.",
	"unqueried": "The setpoint changed and nothing has been read back since.",
	"mismatch": "The instrument answered, but with a different value. Usually the instrument snapping to its own grid rather than an error - compare the two numbers.",
	"query_error": "The value could not be read at all.",
}

# What each colour means, in a few words - the legend in the detail window. The *_TEXT tables above
# are the full explanations, shown on hover.
VERIFICATION_SHORT = {
	"confirmed": "Watched working on hardware",
	"roundtrip": "Read back on hardware",
	"untested": "Never checked on hardware",
	"broken": "Failed on hardware",
	"unavailable": "Hardware can't do this",
	"unknown": "No records to check",
}

SEND_SHORT = {
	"sent": "Sent successfully",
	"unsent": "Not sent yet",
	"failed": "Send failed",
}

VALUE_SHORT = {
	"match": "Matches setpoint",
	"unqueried": "Not read back yet",
	"mismatch": "Differs from setpoint",
	"query_error": "Could not read",
}

# The three lamps, in the order they sit on a control, top to bottom: (key, name, colours, short
# meanings, full explanations). One table, so the control and its detail window cannot disagree.
LAMP_KINDS = (
	("verification", "Verification", VERIFICATION_COLORS, VERIFICATION_SHORT, VERIFICATION_TEXT),
	("send", "Setpoint", SEND_COLORS, SEND_SHORT, SEND_TEXT),
	("value", "Measurement", VALUE_COLORS, VALUE_SHORT, VALUE_TEXT),
)

# SI prefixes offered by the unit selector, largest first. `µ` is spelled with the MICRO SIGN so it
# renders on every platform without a font that has GREEK SMALL LETTER MU.
#
# One of three copies of SI-prefix logic (the others are in Siglent_SDG2000X_dvr.py and
# stardust.units). Consolidating them into a stardust unit registry is designed but deferred - see
# stardust/docs/units_design.md and todo_list.md P11.
UNIT_PREFIXES = (
	("T", 1e12), ("G", 1e9), ("M", 1e6), ("k", 1e3), ("", 1.0),
	("m", 1e-3), ("µ", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15),
)

_VERIFICATION_REPORT_CACHE = {}

def _verification_report(driver_cls, idn:str=None) -> dict:
	''' capability_report() for one driver class, cached.

	A panel builds dozens of controls, and each report re-reads the YAML and re-hashes every
	method's source - once per panel is fine, once per widget is not.
	'''

	key = (driver_cls, idn)

	if key not in _VERIFICATION_REPORT_CACHE:
		try:
			from constellation.verification import capability_report
			_VERIFICATION_REPORT_CACHE[key] = capability_report(driver_cls, idn=idn)
		except Exception:
			# A missing/corrupt records file must not take a GUI down with it. Every method then
			# reads as "unknown", which is the honest answer and fails closed anyway.
			_VERIFICATION_REPORT_CACHE[key] = {}

	return _VERIFICATION_REPORT_CACHE[key]

def clear_verification_cache():
	''' Forgets cached verification reports. Call after a hardware run rewrites verification.yaml
	in the same process. '''

	_VERIFICATION_REPORT_CACHE.clear()

def verification_indicator(bridge, method_names) -> tuple:
	''' Resolves the verification lamp for one parameter.

	Args:
		bridge (InstrumentBridge): The control's bridge. Only an OwningBridge has a Driver to ask;
			an ObserverBridge watches someone else's instrument over labmesh and has no local
			driver class, so it reports "unknown" rather than guessing.
		method_names (iterable): The driver methods this control drives, normally the set/get
			pair.

	Returns:
		tuple: (key, detail_lines). `key` indexes VERIFICATION_COLORS; `detail_lines` is a list of
			"method(): status - why" strings.

	Both halves of a set/get pair are considered and the WEAKEST wins, matching how the hardware
	suite records them: neither half can be verified without the other, so a confirmed setter
	paired with an unverified getter is not a verified parameter.
	'''

	driver = getattr(bridge, "driver", None)
	if driver is None:
		return "unknown", ["no local driver - this panel is observing an instrument owned by another process"]

	idn = getattr(getattr(driver, "id", None), "idn_model", None) or None
	report = _verification_report(type(driver), idn=idn)

	# Rank low-to-high; the lamp shows the worst answer among the methods involved.
	order = ["unavailable", "broken", "unknown", "untested", "roundtrip", "confirmed"]
	worst = None
	lines = []

	for name in method_names:

		if name is None:
			continue

		entry = report.get(name)
		if entry is None:
			key, detail = "unknown", "no record"
		else:
			key, detail = _verification_key(entry)

		lines.append(f"{name}(): {key} - {detail}")

		if worst is None or order.index(key) < order.index(worst):
			worst = key

	return (worst or "unknown"), lines

def _verification_key(entry:dict) -> tuple:
	''' Maps one capability_report() entry onto a lamp colour and an explanation. '''

	from constellation.verification import VerificationStatus

	status = entry.get("status")
	detail = entry.get("detail")

	# The decorator's reason string, or the winning record - both are worth showing verbatim,
	# because "why" is the whole reason someone opened the detail window.
	if isinstance(detail, dict):
		note = ", ".join(f"{k}={detail[k]}" for k in ("model", "firmware", "date", "by") if detail.get(k))
	else:
		note = detail or ""

	if status == VerificationStatus.CONFIRMED:
		return "confirmed", note or "a person confirmed the physical effect"
	if status == VerificationStatus.ROUNDTRIP:
		return "roundtrip", (note + " (self-consistent; nobody watched the instrument)").strip()
	if status == VerificationStatus.FAILED:
		return "broken", note or "checked against hardware and it did not work"
	if status in (VerificationStatus.UNAVAILABLE, VerificationStatus.UNIMPLEMENTED):
		return "unavailable", note or status.value

	# Everything else - unverified, and all three staleness flavours - reads as untested. A stale
	# record's claim no longer stands, so it must not be shown as verified; its detail says which
	# way it expired.
	if status in (VerificationStatus.STALE_CODE, VerificationStatus.STALE_FRAMEWORK, VerificationStatus.STALE_FIRMWARE):
		return "untested", f"{status.value} - was verified, but no longer describes this code/instrument"

	return "untested", note or "implemented, never checked against hardware"

class StatusLamp(QWidget):
	''' One coloured dot with a tooltip, clickable to open a parameter's detail window.

	Painted rather than styled. An earlier version was a QLabel carrying a stylesheet with
	`min-width`/`max-width` set to the dot's size - and Qt resolves a widget's stylesheet for its
	tooltip window too, so the tooltip inherited an 11px width cap and rendered as a single
	clipped letter. Painting the dot means the widget carries no stylesheet at all, so there is
	nothing for the tooltip to inherit.
	'''

	clicked = pyqtSignal()

	def __init__(self, size:int=11, parent=None):
		super().__init__(parent)

		self._size = size
		self._color = "#888888"

		self.setFixedSize(size, size)
		self.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))

	@property
	def color(self) -> str:
		return self._color

	def set(self, color:str, tooltip:str):
		self._color = color
		self.setToolTip(tooltip)
		self.update()

	def mousePressEvent(self, event):
		if event.button() == Qt.MouseButton.LeftButton:
			self.clicked.emit()
		super().mousePressEvent(event)

	def paintEvent(self, event):

		painter = QtGui.QPainter(self)
		painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
		painter.setBrush(QtGui.QBrush(QtGui.QColor(self._color)))
		painter.setPen(Qt.PenStyle.NoPen)
		painter.drawEllipse(0, 0, self._size - 1, self._size - 1)
		painter.end()

class IndicatorLight(QWidget):
	''' A large on/off lamp, drawn from the `indicator_1.png` / `indicator_0.png` artwork in
	`assets/`.

	Exists because a checked QPushButton is nearly indistinguishable from an unchecked one under
	several dark themes - the state is carried entirely by a subtle background shade. A separate
	lamp does not depend on the palette at all.

	Deliberately falls back to a painted circle if the artwork cannot be loaded: the `assets/`
	package-data situation has never been verified against a built wheel (todo P18), and a
	control whose state becomes invisible on a bad install is worse than one that looks plain.
	'''

	def __init__(self, size:int=22, on_pixmap=None, off_pixmap=None, parent=None):
		super().__init__(parent)

		self._size = size
		self._state = None

		self._on = self._load(on_pixmap, "indicator_1.png")
		self._off = self._load(off_pixmap, "indicator_0.png")

		self.setFixedSize(size, size)
		self.set_state(None)

	def _load(self, supplied, filename:str):
		''' Accepts a QPixmap or a path from the caller, else loads the packaged artwork. '''

		if isinstance(supplied, QtGui.QPixmap):
			pixmap = supplied
		elif supplied:
			pixmap = QtGui.QPixmap(str(supplied))
		else:
			pixmap = QtGui.QPixmap(os.path.join(ASSETS_DIR, filename))

		if pixmap.isNull():
			return None

		return pixmap.scaled(self._size, self._size, Qt.AspectRatioMode.KeepAspectRatio,
			Qt.TransformationMode.SmoothTransformation)

	def set_state(self, state):
		''' `state`: True (on), False (off), or None (unknown - nothing heard from the
		instrument yet). '''

		self._state = None if state is None else bool(state)
		self.setToolTip({True: "instrument reports ON", False: "instrument reports OFF",
			None: "nothing read back from the instrument yet"}[self._state])
		self.update()

	def paintEvent(self, event):

		pixmap = self._on if self._state else self._off

		painter = QtGui.QPainter(self)
		painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

		if pixmap is not None and self._state is not None:
			x = (self.width() - pixmap.width()) // 2
			y = (self.height() - pixmap.height()) // 2
			painter.drawPixmap(x, y, pixmap)
		else:
			color = "#888888" if self._state is None else ("#2ecc71" if self._state else "#3a3a3a")
			painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))
			painter.setPen(QtGui.QPen(QtGui.QColor("#00000060")))
			painter.drawEllipse(1, 1, self._size - 3, self._size - 3)

		painter.end()

class ActionIcon(QWidget):
	''' The clickable "SP"/"PV" label beside a field: a short caption and a triangle pointing the
	way the data flows - out to the instrument for SP, back from it for PV.

	Drawn rather than loaded from a PNG so it stays crisp on a HiDPI display, follows the
	palette's text colour, and can recolour on hover to show it is clickable. `pixmap=` accepts a
	QPixmap for anyone who does want artwork.
	'''

	clicked = pyqtSignal()

	def __init__(self, text:str, points_left:bool, tooltip:str="", pixmap=None, parent=None):
		super().__init__(parent)

		self._text = text
		self._points_left = points_left
		self._pixmap = pixmap
		self._hover = False
		self._enabled = True

		self.setFixedSize(34, 22)
		self.setToolTip(tooltip)
		self.setCursor(QtGui.QCursor(Qt.CursorShape.PointingHandCursor))

	def setEnabled(self, enabled:bool):
		self._enabled = enabled
		super().setEnabled(enabled)
		self.update()

	def enterEvent(self, event):
		self._hover = True
		self.update()
		super().enterEvent(event)

	def leaveEvent(self, event):
		self._hover = False
		self.update()
		super().leaveEvent(event)

	def mousePressEvent(self, event):
		if self._enabled and event.button() == Qt.MouseButton.LeftButton:
			self.clicked.emit()
		super().mousePressEvent(event)

	def paintEvent(self, event):

		painter = QtGui.QPainter(self)
		painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

		color = self.palette().text().color()
		if not self._enabled:
			color.setAlpha(70)
		elif self._hover:
			color = QtGui.QColor("#5dade2")

		if self._pixmap is not None:
			painter.drawPixmap(self.rect(), self._pixmap)
			painter.end()
			return

		h = self.height()
		tri_w, tri_h = 8, 10
		gap = 2

		# Triangle leads for SP (pointing at the field the value is going into) and trails for PV
		# (pointing out of the field the value came from), so the two rows are distinguishable at
		# a glance without reading the caption.
		if self._points_left:
			tri_x, text_x = 0, tri_w + gap
		else:
			text_x, tri_x = 0, self.width() - tri_w

		painter.setPen(color)
		font = painter.font()
		font.setBold(True)
		painter.setFont(font)
		painter.drawText(QtCore.QRect(text_x, 0, self.width() - tri_w - gap, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignHCenter, self._text)

		top = (h - tri_h) // 2
		triangle = QtGui.QPolygon([
			QtCore.QPoint(tri_x + (0 if self._points_left else tri_w), top + tri_h // 2),
			QtCore.QPoint(tri_x + (tri_w if self._points_left else 0), top),
			QtCore.QPoint(tri_x + (tri_w if self._points_left else 0), top + tri_h),
		])

		painter.setBrush(QtGui.QBrush(color))
		painter.setPen(Qt.PenStyle.NoPen)
		painter.drawPolygon(triangle)
		painter.end()

class ParameterDetailDialog(QDialog):
	''' The "more info" window a parameter's lamps open.

	Three jobs, all of them things a user wants at the moment a lamp goes the wrong colour and
	none of which fit in a tooltip: say what each lamp actually means for *this* parameter, show
	the raw traffic (what was sent, what came back, and the SCPI behind it), and let the display
	be reconfigured while the problem is being looked at.
	'''

	def __init__(self, control, parent=None):
		super().__init__(parent)

		self.control = control

		self.setWindowTitle(f"{control.label} - details")
		self.setMinimumWidth(520)

		# Cmd/Ctrl-W closes the dialog, not the panel behind it (WindowShortcut scope).
		self._window_shortcuts = install_window_shortcuts(self, on_close=self.reject)

		layout = QVBoxLayout()

		header = QLabel(f"<b>{control.label}</b><br><span style='color:gray'>{control.set_method}() / {control.get_method or 'no getter'}()</span>")
		header.setTextFormat(Qt.TextFormat.RichText)
		layout.addWidget(header)
		layout.addWidget(self._separator())

		# --- display options ---
		density = QHBoxLayout()
		density.addWidget(QLabel("Detail shown:"))
		self.view_combo = QComboBox()
		for mode in ParameterView.ORDER:
			self.view_combo.addItem(ParameterView.LABELS[mode], mode)
		self.view_combo.setCurrentIndex(ParameterView.ORDER.index(control.view))
		self.view_combo.activated.connect(self._on_view_chosen)
		density.addWidget(self.view_combo, 1)
		layout.addLayout(density)

		self.lcd_check = QCheckBox("LCD Readout")
		self.lcd_check.setChecked(bool(getattr(control, "lcd", False)))
		self.lcd_check.setEnabled(getattr(control, "supports_lcd", False))
		self.lcd_check.toggled.connect(control.set_lcd)
		layout.addWidget(self.lcd_check)

		self.follow_check = QCheckBox("Update GUI controls from instrument")
		self.follow_check.setToolTip("When the instrument reports a different value, show it in the "
			"setpoint field too. Never while you are typing, and never with a value read before your "
			"last change reached the instrument.")
		self.follow_check.setChecked(control.follow_instrument)
		self.follow_check.toggled.connect(control.set_follow_instrument)
		layout.addWidget(self.follow_check)

		similar = len(control.similar_controls())
		self.similar_button = QPushButton("Apply to similar")
		# Not the control's label: on a per-channel control it names the channel ("Output 1"), so
		# "the other 'Output 1' controls" would name exactly the ones it is NOT applied to.
		self.similar_button.setToolTip(
			f"Apply these options to {similar} similar control{'s' if similar != 1 else ''} on this panel."
			if similar else "No similar controls on this panel.")
		self.similar_button.setEnabled(similar > 0)
		self.similar_button.clicked.connect(control.apply_options_to_similar)

		similar_row = QHBoxLayout()
		similar_row.addWidget(self.similar_button)
		similar_row.addStretch(1)
		layout.addLayout(similar_row)

		layout.addWidget(self._separator())

		# --- the lamps: which is which, what it says now, and what every colour means ---
		# Rows are in the order the lamps sit on the control, top to bottom. Explanations are on
		# hover; the window itself only answers "which lamp is this" and "what does this colour mean".
		self.lamp_rows = {}        # key -> (lamp, current-state label)
		self.name_labels = {}      # key -> lamp name label
		self.legend_entries = {}   # key -> {state: (dot, meaning label)}

		grid = QGridLayout()
		grid.setHorizontalSpacing(10)
		grid.setColumnStretch(4, 1)

		for column, heading in ((1, "Lamp"), (2, "Now"), (3, "Colours")):
			label = QLabel(heading)
			label.setStyleSheet("QLabel { color: gray; }")
			grid.addWidget(label, 0, column)

		for row, (key, name, colors, short, _long) in enumerate(LAMP_KINDS, start=1):

			lamp = StatusLamp(13)
			lamp.setCursor(QtGui.QCursor(Qt.CursorShape.ArrowCursor))

			name_label = QLabel()
			name_label.setTextFormat(Qt.TextFormat.RichText)

			now = QLabel()
			now.setTextFormat(Qt.TextFormat.RichText)

			legend = QFrame()
			legend.setFrameShape(QFrame.Shape.StyledPanel)
			legend_grid = QGridLayout()
			legend_grid.setContentsMargins(6, 3, 6, 3)
			legend_grid.setVerticalSpacing(1)

			entries = {}
			for i, (state, color) in enumerate(colors.items()):
				dot = StatusLamp(9)
				dot.setCursor(QtGui.QCursor(Qt.CursorShape.ArrowCursor))
				dot.set(color, "")
				meaning = QLabel(short.get(state, state))
				meaning.setTextFormat(Qt.TextFormat.RichText)
				meaning.setToolTip(f"{state}: {_long.get(state, '')}")
				legend_grid.addWidget(dot, i, 0)
				legend_grid.addWidget(meaning, i, 1)
				entries[state] = (dot, meaning)

			legend.setLayout(legend_grid)

			grid.addWidget(lamp, row, 0, Qt.AlignmentFlag.AlignTop)
			grid.addWidget(name_label, row, 1, Qt.AlignmentFlag.AlignTop)
			grid.addWidget(now, row, 2, Qt.AlignmentFlag.AlignTop)
			grid.addWidget(legend, row, 3)

			self.lamp_rows[key] = (lamp, now)
			self.name_labels[key] = name_label
			self.legend_entries[key] = entries

		layout.addLayout(grid)
		layout.addWidget(self._separator())

		# --- traffic ---
		self.traffic = QLabel()
		self.traffic.setTextFormat(Qt.TextFormat.RichText)
		self.traffic.setWordWrap(True)
		self.traffic.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
		layout.addWidget(self.traffic)

		# --- actions ---
		buttons = QHBoxLayout()
		self.resend_button = QPushButton("Re-send setpoint")
		self.resend_button.clicked.connect(control.resend)
		self.requery_button = QPushButton("Re-read value")
		self.requery_button.clicked.connect(control.requery)
		self.requery_button.setEnabled(control.get_method is not None)
		buttons.addWidget(self.resend_button)
		buttons.addWidget(self.requery_button)
		buttons.addStretch(1)

		close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
		close.rejected.connect(self.reject)
		buttons.addWidget(close)
		layout.addLayout(buttons)

		self.setLayout(layout)

		# Live, so a dialog left open while the instrument is poked keeps telling the truth.
		control.changed.connect(self.refresh)
		self.refresh()

	def _separator(self):
		line = QFrame()
		line.setFrameShape(QFrame.Shape.HLine)
		line.setFrameShadow(QFrame.Shadow.Sunken)
		return line

	def _on_view_chosen(self, index:int):
		self.control.set_view(self.view_combo.itemData(index))

	def refresh(self):

		control = self.control

		verification_key = control.verification_key
		send_key = control.send_status()
		value_key = control.value_status()

		current = {"verification": verification_key, "send": send_key, "value": value_key}
		extra = {
			"verification": "\n".join(control.verification_lines),
			"send": control.send_error,
			"value": control.query_error,
		}

		shown = {id(lamp) for lamp in control.visible_lamps()}
		lamp_of = {"verification": control.lamp_verification, "send": control.lamp_send,
			"value": control.lamp_value}

		for key, name, colors, short, long_text in LAMP_KINDS:
			self._set_row(key, name, current[key], colors, short, long_text, extra[key],
				on_control=id(lamp_of[key]) in shown)

		self.resend_button.setEnabled(control.setpoint is not None and verification_key != "unavailable")

		if self.view_combo.currentIndex() != ParameterView.ORDER.index(control.view):
			self.view_combo.setCurrentIndex(ParameterView.ORDER.index(control.view))

		command, response = control.last_scpi()

		rows = [
			f"<b>Last sent:</b> {_html(control.setpoint)}",
			f"<b>Last received:</b> {_html(control.confirmed)}",
			f"<b>Driver call:</b> <code>{_html(control.call_signature())}</code>",
		]

		# Errors are data a user debugging needs to see without hovering over anything.
		for label, error in (("Send error", control.send_error), ("Read error", control.query_error)):
			if error:
				rows.append(f"<b>{label}:</b> <span style='color:#e74c3c'>{_html(error)}</span>")

		if command is not None:
			rows.append(f"<b>SCPI sent:</b> <code>{_html(command)}</code>")
			if response is not None:
				rows.append(f"<b>SCPI reply:</b> <code>{_html(response)}</code>")
		else:
			# Not an error - a dummy driver never touches a relay, and an ObserverBridge issues
			# the call in another process entirely, so there is no local SCPI to show.
			rows.append("<span style='color:gray'>No SCPI recorded for this parameter yet.</span>")

		self.traffic.setText("<br>".join(rows))

	def _set_row(self, key:str, name:str, state:str, colors:dict, short:dict, long_text:dict,
			extra:str, on_control:bool):
		''' One lamp's row: its colour and state now, and which legend entry that is. '''

		lamp, now = self.lamp_rows[key]

		tooltip = f"{name}: {state}\n{long_text.get(state, '')}" + (f"\n\n{extra}" if extra else "")

		lamp.set(colors.get(state, "#888888"), tooltip)
		now.setText(f"<b>{_html(state)}</b>")
		now.setToolTip(tooltip)

		# Compact hides a lamp; say so, or a user counts two dots and three rows and cannot match
		# them up.
		label = self.name_labels[key]
		label.setText(f"<b>{name}</b>" if on_control else
			f"<b>{name}</b><br><span style='color:gray'>not shown in this view</span>")
		label.setToolTip(tooltip)

		for entry_state, (dot, meaning) in self.legend_entries[key].items():
			text = _html(short.get(entry_state, entry_state))
			meaning.setText(f"<b>{text}</b>" if entry_state == state else text)

def _html(value) -> str:
	''' Escapes a value for the rich-text labels above. An IDN or a SCPI string can legitimately
	contain characters that would otherwise be read as markup. '''

	import html

	return html.escape("None" if value is None else str(value))

class _ParameterControlBase(_TrackedControlBase):
	''' Shared machinery for the SP/PV family: the lamp state, the switchable layout, the optional
	unit-prefix scaling, and the two action buttons. Subclasses supply the SP editor, the PV
	widget, and how to render a value.

	Deliberately built on _TrackedControlBase rather than replacing it - the setpoint/confirmed
	bookkeeping there is already correct, and the existing Tracked* controls keep working
	untouched.
	'''

	# Emitted whenever any displayed state changes, so an open detail dialog can follow along.
	changed = pyqtSignal()

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str,
			set_args:callable=None, get_method:str=None, get_args:tuple=(), unit:str="",
			tolerance:float=0.01, abs_tolerance:float=0.0, stale_after_s:float=5.0,
			view:str=ParameterView.COMPACT, edit_width:int=90,
			prefixes=None, auto_prefix:bool=True):

		super().__init__(bridge, label, get, set_method, set_args, stale_after_s)

		# Every category API is a set_x/get_x pair, so the getter's name is derivable - but it can
		# be given explicitly for anything that doesn't follow the convention.
		if get_method is None and set_method.startswith("set_"):
			get_method = "get_" + set_method[4:]

		self.label = label
		self.get_method = get_method
		self.get_args = tuple(get_args)
		self.tolerance = tolerance
		self.abs_tolerance = abs_tolerance
		self.unit = unit
		self.view = view
		self.edit_width = edit_width
		self.supports_lcd = False
		self.lcd = False

		self.send_error = ""
		self.query_error = ""
		self._send_state = "unsent"
		self._awaiting_readback = False
		self._online = True

		# The last value the USER asked for, as distinct from _setpoint, which also mirrors the
		# instrument until the user touches the control. Auto-send re-sends only these: pushing a
		# mirrored value back would just echo the instrument to itself.
		self._user_setpoint = None

		# When the instrument reports a value different from the setpoint, adopt it as the new
		# setpoint - so a change made on the front panel (or by another program) shows up in the SP
		# field, not only on the PV row. See _should_adopt() for when this is held off.
		self.follow_instrument = True

		# Argument tuples of this control's set requests the bridge has not answered yet. A state
		# read taken before one of these ran would carry the OLD value, so nothing is adopted until
		# the list is empty: the bridge runs commands in order and answers each one before the state
		# read that follows it, so the first state after the last answer is guaranteed fresh.
		# Matched on arguments, not just the method name - every channel's frequency control calls
		# set_frequency, and channel 2's answer must not release channel 1.
		self._in_flight = []

		# True while the user has typed something they haven't committed. See _display_setpoint:
		# a background poll must never overwrite half-typed input.
		self._dirty = False

		self.title_label = QLabel(label)
		self.title_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

		self.inline_label = QLabel(f"{label}:")

		self.lamp_verification = StatusLamp()
		self.lamp_send = StatusLamp()
		self.lamp_value = StatusLamp()

		for lamp in (self.lamp_verification, self.lamp_send, self.lamp_value):
			lamp.clicked.connect(self.show_details)

		self.sp_icon = ActionIcon("SP", points_left=True, tooltip="Send this setpoint to the instrument again")
		self.pv_icon = ActionIcon("PV", points_left=False, tooltip="Re-read this value from the instrument")
		self.sp_icon.clicked.connect(self.resend)
		self.pv_icon.clicked.connect(self.requery)
		self.pv_icon.setEnabled(self.get_method is not None)

		# --- unit prefix selector ---
		self._prefixes = self._resolve_prefixes(prefixes)
		self.auto_prefix = auto_prefix and self._prefixes is not None
		self._prefix_chosen = False
		self.scale = 1.0

		self.prefix_combo = QComboBox()
		if self._prefixes is not None:
			for symbol, factor in self._prefixes:
				self.prefix_combo.addItem(f"{symbol}{unit}", factor)
			self.prefix_combo.setCurrentIndex([f for _, f in self._prefixes].index(1.0) if any(f == 1.0 for _, f in self._prefixes) else 0)
			self.prefix_combo.activated.connect(self._on_prefix_chosen)

		self.unit_sp = QLabel(unit)
		self.unit_pv = QLabel(unit)

		self._dialog = None

		# Resolved once: verification is a property of the code and the records, not of anything
		# happening at runtime, so it cannot change while a panel is open.
		self.verification_key, self.verification_lines = verification_indicator(bridge, (set_method, get_method))

	def _resolve_prefixes(self, prefixes):
		''' `prefixes=True` gives the standard SI set, a sequence of symbols narrows it, and None
		(the default) turns the selector off entirely. '''

		if prefixes is None or prefixes is False:
			return None

		if prefixes is True:
			return UNIT_PREFIXES

		wanted = set(prefixes)

		return tuple((symbol, factor) for symbol, factor in UNIT_PREFIXES if symbol in wanted)

	@property
	def has_prefixes(self) -> bool:
		return self._prefixes is not None

	# --- read-only views of the state, for the detail dialog ----------------

	@property
	def setpoint(self):
		return self._setpoint

	@property
	def confirmed(self):
		return self._confirmed

	def call_signature(self) -> str:
		''' The driver call this control makes, as it would be written by hand. '''

		if self._setpoint is None:
			return f"{self.set_method}(...)"

		args = ", ".join(repr(a) for a in self.set_args(self._setpoint))

		return f"{self.set_method}({args})"

	def last_scpi(self) -> tuple:
		''' (command, response) for whichever of this parameter's methods last talked to the
		instrument, or (None, None). Read from the bridge, which attributes SCPI per method - the
		relay only knows the most recent command globally. '''

		journal = getattr(self.bridge, "last_scpi", None)
		if not journal:
			return None, None

		for name in (self.set_method, self.get_method):
			if name in journal:
				return journal[name]

		return None, None

	# --- unit scaling -------------------------------------------------------

	def _on_prefix_chosen(self, index:int):
		''' Changing the prefix rescales the display only. Nothing is sent: the user asked to see
		the same quantity in different units, not to change it. '''

		self._prefix_chosen = True
		self.scale = self.prefix_combo.itemData(index)
		self.unit_pv.setText(self.prefix_combo.currentText())
		self._dirty = False
		self._refresh_display()

	def _maybe_autoscale(self, value):
		''' Picks the prefix that puts the first real value in a readable range.

		Only ever fires once, and never after the user has touched the selector - a control whose
		units move under you while you are reading it is worse than one that shows 0.002.
		'''

		if not self.auto_prefix or self._prefix_chosen or self._prefixes is None:
			return

		try:
			magnitude = abs(float(value))
		except (TypeError, ValueError):
			return

		if magnitude == 0:
			return   # 0 is 0 in every prefix, and would otherwise pin the selector at femto

		for index, (symbol, factor) in enumerate(self._prefixes):
			if magnitude >= factor:
				self.prefix_combo.setCurrentIndex(index)
				self.scale = factor
				self.unit_pv.setText(self.prefix_combo.currentText())
				break

		self._prefix_chosen = True

	def _to_display(self, value):
		''' Instrument units -> the units the user is looking at. '''

		if value is None or self.scale == 1.0:
			return value

		try:
			return float(value) / self.scale
		except (TypeError, ValueError):
			return value

	def _from_display(self, value):
		''' The units the user typed in -> instrument units. '''

		return value if self.scale == 1.0 else value * self.scale

	# --- layout / density ---------------------------------------------------

	def _build_layout(self, sp_editor, pv_widget):
		''' Called once by each subclass, after it has built its editor and PV widget. '''

		self.sp_editor = sp_editor
		self.pv_widget = pv_widget

		# Extra space in a container goes BETWEEN controls, not inside them - in BOTH directions.
		# Every piece here has a fixed or natural size and the widget as a whole refuses to grow,
		# so a resized panel spreads its slack across the gaps between controls instead of
		# re-spacing each control's internals.
		# Horizontally Maximum (may shrink in a cramped panel, never grows); vertically Fixed,
		# because Maximum also permits SHRINKING - and a control switched to the taller full view
		# was then squashed into the row height the compact one had needed.
		self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)

		# The whole layout skeleton is built ONCE here and then only ever re-filled. Switching
		# density used to destroy and recreate every layout in the control, which churns Qt's
		# widget tree (and, on macOS, its text-input contexts) for no reason - a density switch
		# moves existing widgets around, it does not need new containers.
		self._root = QVBoxLayout()
		self._root.setContentsMargins(4, 2, 4, 2)
		self._root.setSpacing(2)
		self.setLayout(self._root)

		self._body = QHBoxLayout()
		self._body.setSpacing(6)

		self._rows = QVBoxLayout()
		self._rows.setSpacing(2)

		self._sp_row = QHBoxLayout()
		self._sp_row.setSpacing(4)

		self._pv_row = QHBoxLayout()
		self._pv_row.setSpacing(4)

		self._lamp_column = QVBoxLayout()
		self._lamp_column.setSpacing(3)

		self._apply_view()

		# A method the hardware cannot do, or that nobody has written, gets a visibly dead
		# control rather than one that raises FeatureUnavailable when clicked.
		if self.verification_key == "unavailable":
			for widget in (self.sp_editor, self.sp_icon, self.pv_icon, self.prefix_combo):
				widget.setEnabled(False)

		self._refresh_display()

	def set_view(self, view:str):
		''' Switches density in place. Safe to call at any time, including from the detail
		dialog while the panel is live. '''

		if view not in ParameterView.ORDER or view == self.view:
			return

		self.view = view
		self._apply_view()
		self._refresh_display()

	def set_lcd(self, enabled:bool):
		''' Switches the measured-value readout between a text field and a QLCDNumber. Only
		meaningful for numeric parameters, and only visible in the full view. '''

		if not self.supports_lcd or bool(enabled) == self.lcd:
			return

		self.lcd = bool(enabled)
		self._apply_view()
		self._refresh_display()

	def show_sp_icon(self) -> bool:
		''' The SP button is a full-view affordance. In compact the label is doing that job, and
		the detail window still offers "Re-send setpoint". '''

		return self.view == ParameterView.FULL

	def show_inline_label(self) -> bool:
		return self.view == ParameterView.COMPACT

	def visible_lamps(self) -> list:
		''' Which lamps this density shows. See ParameterView for why compact drops the
		verification lamp rather than a runtime one. '''

		if self.view == ParameterView.FULL:
			return [self.lamp_verification, self.lamp_send, self.lamp_value]

		return [self.lamp_send, self.lamp_value]

	def _pv_row_widget(self):
		''' Which widget shows the measured value. Subclasses override to swap in an LCD or an
		indicator lamp. '''

		return self.pv_widget

	def _managed_widgets(self) -> tuple:
		''' Every widget this control owns, placed in the current view or not.

		Used to keep C++ ownership with the control across a layout rebuild. A widget the current
		view does not place (an unused LCD, say) has no parent layout holding it, and only this
		keeps it alive.
		'''

		return (self.title_label, self.inline_label, self.sp_icon, self.pv_icon, self.sp_editor,
			self.pv_widget, self.unit_sp, self.unit_pv, self.prefix_combo,
			self.lamp_verification, self.lamp_send, self.lamp_value) + self._extra_hidable()

	def _apply_view(self):

		full = self.view == ParameterView.FULL

		# Empty every layout, keeping the layouts themselves. takeAt() detaches an item without
		# touching the widget's parent, so nothing is created or destroyed here - the widgets are
		# simply re-placed.
		for layout in (self._sp_row, self._pv_row, self._lamp_column, self._rows, self._body, self._root):
			_drain_layout(layout)

		for widget in self._managed_widgets():
			if widget.parent() is not self:
				widget.setParent(self)
			widget.setVisible(False)

		# --- setpoint row ---
		if self.show_inline_label():
			self.inline_label.setVisible(True)
			self._sp_row.addWidget(self.inline_label)

		if self.show_sp_icon():
			self.sp_icon.setVisible(True)
			self._sp_row.addWidget(self.sp_icon)

		self._add_sp_editor(self._sp_row)

		# The prefix selector sits immediately right of the setpoint field, and doubles as the
		# unit label - so there is exactly one place the units are stated for the SP row.
		if self.has_prefixes:
			self.prefix_combo.setVisible(True)
			self._sp_row.addWidget(self.prefix_combo)
		elif self.unit:
			self.unit_sp.setVisible(True)
			self._sp_row.addWidget(self.unit_sp)

		# Both rows get a trailing stretch so both pack LEFT. Without it the SP row spread its
		# slack between its widgets while the PV row (which has one) stayed put, and the two rows'
		# indicators drifted out of column - which is exactly the alignment that makes an SP row
		# above a PV row readable as "asked" above "actually".
		self._sp_row.addStretch(1)
		self._rows.addLayout(self._sp_row)

		# --- measured row ---
		if full and self.show_pv_row():

			self.pv_icon.setVisible(True)
			self._pv_row.addWidget(self.pv_icon)

			pv = self._pv_row_widget()
			pv.setVisible(True)
			self._pv_row.addWidget(pv)

			# The PV row's unit label mirrors the selector, so both rows always read in the same
			# units - which is the entire point of scaling them together.
			if self.has_prefixes or self.unit:
				self.unit_pv.setText(self.prefix_combo.currentText() if self.has_prefixes else self.unit)
				self.unit_pv.setVisible(True)
				self._pv_row.addWidget(self.unit_pv)

			self._pv_row.addStretch(1)
			self._rows.addLayout(self._pv_row)

		# --- lamps ---
		# Stretch on BOTH sides centres the lamp column against the rows it annotates. With a
		# stretch only underneath, the lamps rode the top of whatever cell the control was placed
		# in and drifted away from the control they describe as soon as the row got taller.
		self._lamp_column.addStretch(1)

		for lamp in self.visible_lamps():
			lamp.setVisible(True)
			self._lamp_column.addWidget(lamp)

		self._lamp_column.addStretch(1)

		self._body.addLayout(self._rows)
		self._body.addLayout(self._lamp_column)

		# The title sits ABOVE the row-plus-lamps block rather than inside it, so the lamp column
		# centres on the fields it annotates. Centring it over the title as well pushed the lamps
		# up out of line with the rows whose status they report.
		if full and self.show_title():
			self.title_label.setVisible(True)
			self._root.addWidget(self.title_label)

		self._root.addLayout(self._body)

		# Any height beyond what the rows need pools here rather than being shared out between
		# them. The size policy already stops a well-behaved container from growing the widget at
		# all; this keeps the rows packed even when something resizes it directly.
		self._root.addStretch(1)

		# Switching density changes how much room this control needs. Without telling the parent,
		# a container keeps the old row height and clips the taller view.
		self.updateGeometry()

	def show_title(self) -> bool:
		return True

	def show_pv_row(self) -> bool:
		return True

	def _extra_hidable(self) -> tuple:
		return ()

	def _add_sp_editor(self, sp_row):
		sp_row.addWidget(self.sp_editor)
		self.sp_editor.setVisible(True)

	# --- user actions -------------------------------------------------------

	def show_details(self):
		''' Opens (or raises) this parameter's detail window. '''

		if self._dialog is None:
			self._dialog = ParameterDetailDialog(self, parent=self.window())
			self._dialog.finished.connect(self._on_dialog_closed)

		self._dialog.show()
		self._dialog.raise_()
		self._dialog.activateWindow()

	def _on_dialog_closed(self, result):
		self._dialog = None

	def resend(self):
		''' Re-sends the current setpoint. Does nothing if the user has never set one - there is
		no defensible value to send, and inventing one would write to an instrument on a click
		the user thought was a refresh. '''

		if self._setpoint is None:
			return

		self._send_state = "unsent"
		self.send_error = ""
		self._awaiting_readback = True
		self._in_flight.append(tuple(self.set_args(self._setpoint)))
		self._refresh_display()
		self.bridge.request(self.set_method, *self.set_args(self._setpoint))

	def requery(self):
		''' Asks the instrument for the value again. '''

		if self.get_method is None:
			return

		self.bridge.request(self.get_method, *self.get_args)

	# --- state machine ------------------------------------------------------

	def _matches(self, a, b) -> bool:
		''' Whether a read-back agrees with a setpoint.

		Instruments quantize - a scope asked for 0.55 V/div reports 0.5 - so exact equality
		(which is what the single-lamp Tracked* controls use) marks a correctly-working
		instrument as mismatched forever. Numeric comparisons get a relative tolerance;
		everything else falls back to equality.
		'''

		if a is None or b is None:
			return False

		if isinstance(a, bool) or isinstance(b, bool):
			return bool(a) == bool(b)

		try:
			a_f, b_f = float(a), float(b)
		except (TypeError, ValueError):
			return a == b

		return abs(a_f - b_f) <= max(self.abs_tolerance, abs(b_f) * self.tolerance)

	@property
	def user_setpoint(self):
		''' The last value set from this control, or None if it has only ever mirrored the
		instrument. '''
		return self._user_setpoint

	def set_follow_instrument(self, enabled:bool):
		''' Whether the setpoint follows values read back from the instrument. '''
		self.follow_instrument = bool(enabled)
		self.changed.emit()

	def panel(self):
		''' The InstrumentWidget this control sits on, or None if it is not on one. '''

		widget = self.parentWidget()
		while widget is not None and not isinstance(widget, InstrumentWidget):
			widget = widget.parentWidget()

		return widget

	def similar_controls(self) -> list:
		''' The other controls on this panel that are the same parameter - the same kind of control
		driving the same method. On a multi-channel instrument that is this parameter on every other
		channel: every channel's output toggle, every channel's frequency box. '''

		panel = self.panel()
		if panel is None:
			return []

		return [c for c in panel.parameter_controls()
			if c is not self and type(c) is type(self) and c.set_method == self.set_method]

	def apply_options_to_similar(self) -> int:
		''' Copies this control's display options (density, LCD, following the instrument) to every
		similar control. Returns how many were changed. '''

		similar = self.similar_controls()

		for other in similar:
			other.set_view(self.view)
			if self.supports_lcd and other.supports_lcd:
				other.set_lcd(self.lcd)
			other.set_follow_instrument(self.follow_instrument)
			other._default_view_applied = True

		return len(similar)

	def _should_adopt(self, value) -> bool:
		''' Whether a value just read from the instrument should replace the setpoint.

		Only when it is genuinely different (beyond tolerance - a quantized read-back of the user's
		own value is not a change of mind), the user is not part-way through typing a new value, and
		every set request this control has made has been answered, so the value cannot predate the
		user's latest change.
		'''

		return (self.follow_instrument and not self._dirty and not self._in_flight
			and value is not None and self._setpoint is not None
			and not self._matches(value, self._setpoint))

	def _user_changed(self, new_value):
		self._send_state = "unsent"
		self.send_error = ""
		self._awaiting_readback = True
		self._dirty = False
		self._user_setpoint = new_value
		self._in_flight.append(tuple(self.set_args(new_value)))
		super()._user_changed(new_value)

	def _on_command_result(self, method_name, args, success, result):

		if method_name == self.set_method:
			self._send_state = "sent" if success else "failed"
			self.send_error = "" if success else str(result)

			# Answered - successfully or not, the next state read comes after this command.
			if tuple(args) in self._in_flight:
				self._in_flight.remove(tuple(args))

		if method_name == self.get_method:
			self.query_error = "" if success else str(result)

		super()._on_command_result(method_name, args, success, result)
		self._refresh_display()

	def _on_state_changed(self, state):

		try:
			value = self.get(state)
		except Exception:
			return

		self._maybe_autoscale(value)
		self._awaiting_readback = False
		self.query_error = ""

		if self._should_adopt(value):
			self._setpoint = value

		super()._on_state_changed(state)

	def _on_connection_changed(self, online):
		self._online = online
		super()._on_connection_changed(online)
		self._refresh_display()

	def send_status(self) -> str:
		# Deliberately NOT gated on self._pending. In the base class `_pending` means "waiting for
		# a read-back", which is the value lamp's question - letting it mask the send lamp would
		# re-merge the two facts this widget exists to keep apart.
		return self._send_state

	def value_status(self) -> str:

		if self.query_error or not self._online:
			return "query_error"
		if self._confirmed is None or self._awaiting_readback:
			return "unqueried"
		if self._setpoint is None or self._matches(self._confirmed, self._setpoint):
			return "match"

		return "mismatch"

	def _format(self, value) -> str:
		return "" if value is None else str(value)

	def _refresh_lamps(self):

		verification = self.verification_key
		send = self.send_status()
		value = self.value_status()

		hint = "  (click for details)"

		self.lamp_verification.set(VERIFICATION_COLORS.get(verification, "#888888"),
			f"Verification: {verification}\n{VERIFICATION_TEXT.get(verification, '')}\n"
			+ "\n".join(self.verification_lines) + hint)

		self.lamp_send.set(SEND_COLORS[send],
			f"Setpoint: {send}\n{SEND_TEXT[send]}\nsetpoint = {self._setpoint}"
			+ (f"\n{self.send_error}" if self.send_error else "") + hint)

		self.lamp_value.set(VALUE_COLORS[value],
			f"Measurement: {value}\n{VALUE_TEXT[value]}\nmeasured = {self._confirmed}"
			+ (f"\n{self.query_error}" if self.query_error else "") + hint)

	def _display(self, confirmed_value, setpoint_value, status):

		self._display_setpoint(setpoint_value if setpoint_value is not None else confirmed_value)
		self._display_measured(confirmed_value)
		self._refresh_lamps()
		self.changed.emit()

	def _display_setpoint(self, value):
		raise NotImplementedError

	def _display_measured(self, value):
		raise NotImplementedError


def _scpi_locale():
	''' A locale that reads numbers the way Python does: "." decimal point, no group separators.

	Numeric fields are displayed with str() and parsed with float(), which ignore the system
	locale entirely, so their validators must too.
	'''

	locale = QtCore.QLocale.c()
	locale.setNumberOptions(QtCore.QLocale.NumberOption.RejectGroupSeparator)

	return locale

def _drain_layout(layout):
	''' Removes every item from one layout without disturbing any widget's parent.

	takeAt() detaches a QWidgetItem but leaves the widget itself parented where it was, which is
	exactly what re-filling a layout needs: the control keeps its widgets, the layout keeps
	nothing. Deliberately shallow - each layout in the skeleton is drained by name, so recursing
	would empty the same containers twice.
	'''

	while layout.count():
		layout.takeAt(0)

class ParameterBox(_ParameterControlBase):
	''' A numeric parameter. Example:

		ParameterBox(bridge, "Volts/div", get=lambda s: s.channels[1].div_volt,
			set_method="set_div_volt", set_args=lambda v: (1, v), get_args=(1,), unit="V")

	With a unit-prefix selector, so a user types "2" and picks "ms" rather than typing "0.002":

		ParameterBox(bridge, "Offset", get=lambda s: s.offset_time,
			set_method="set_offset_time", unit="s", prefixes=True)
	'''

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str,
			set_args:callable=None, get_method:str=None, get_args:tuple=(), validator=None,
			unit:str="", tolerance:float=0.01, abs_tolerance:float=0.0, stale_after_s:float=5.0,
			view:str=ParameterView.COMPACT, edit_width:int=90, prefixes=None,
			auto_prefix:bool=True, lcd:bool=True, lcd_digits:int=6):

		super().__init__(bridge, label, get, set_method, set_args, get_method, get_args, unit,
			tolerance, abs_tolerance, stale_after_s, view, edit_width, prefixes, auto_prefix)

		self.supports_lcd = True
		self.lcd = bool(lcd)

		self.edit = QLineEdit()
		if validator is not None:
			# The field is written with str(float) and read back with float(), both of which use
			# "." whatever the system locale says. A validator left on the system locale disagrees
			# wherever "." is the THOUSANDS separator (German, Dutch, ... regions): its fixup()
			# strips the "." on every commit, so a field showing "2.0" commits as "20" - and then
			# "200", "2000" on each subsequent commit.
			validator.setLocale(_scpi_locale())
			self.edit.setValidator(validator)
		self.edit.setFixedWidth(edit_width)
		self.edit.editingFinished.connect(self._on_edited)

		# textEdited fires only for user typing, never for setText() - which is exactly the
		# distinction _display_setpoint needs.
		self.edit.textEdited.connect(self._on_text_edited)

		self.pv_display = QLineEdit()
		self.pv_display.setReadOnly(True)
		self.pv_display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
		self.pv_display.setFixedWidth(edit_width)

		self.pv_lcd = QLCDNumber()
		self.pv_lcd.setDigitCount(lcd_digits)
		self.pv_lcd.setSegmentStyle(QLCDNumber.SegmentStyle.Flat)
		self.pv_lcd.setSmallDecimalPoint(True)
		# Taller than the text field it replaces - seven-segment digits are unreadable at
		# line-edit height, which defeats the point of asking for an LCD.
		self.pv_lcd.setFixedSize(max(edit_width, lcd_digits * 16), 36)

		self._build_layout(self.edit, self.pv_display)

	def _extra_hidable(self):
		return (self.pv_lcd,)

	def _pv_row_widget(self):
		return self.pv_lcd if self.lcd else self.pv_display

	def _on_text_edited(self, text):
		self._dirty = True

	def _on_edited(self):

		# editingFinished fires on Return and on focus-out whether or not anything was typed.
		# Only a real edit may write to the instrument: clicking through a field must never send
		# it a value, and when the GUI is only monitoring an instrument this is the guarantee that
		# nothing is written unless the user changed something.
		if not self._dirty:
			self._refresh_display()   # also undoes anything the validator's fixup() did
			return

		try:
			value = self._from_display(float(self.edit.text()))
		except ValueError:
			self._dirty = False
			self._refresh_display()   # revert to the last known-good display
			return

		if value == self._setpoint:
			self._dirty = False
			self._refresh_display()
			return

		self._user_changed(value)

	def _display_setpoint(self, value):

		# Never clobber input the user has typed but not committed. This used to be guarded on
		# `hasFocus()`, which is False whenever the window is not the active one - so a background
		# poll would silently replace half-typed text with the last known value. A field showing
		# "0" ate "0.002" and looked like it had rounded.
		if value is None or self._dirty:
			return

		self.edit.setText(self._format(self._to_display(value)))

	def _display_measured(self, value):

		shown = self._to_display(value)

		self.pv_display.setText(self._format(shown))

		if shown is None:
			self.pv_lcd.display("")
		else:
			try:
				self.pv_lcd.display(float(shown))
			except (TypeError, ValueError):
				self.pv_lcd.display("")

class ParameterToggle(_ParameterControlBase):
	''' An on/off parameter, with a large state lamp to the left of the button.

	The lamp is there because a checked QPushButton is nearly indistinguishable from an unchecked
	one under several dark themes - the state is carried entirely by a subtle background shade,
	which is exactly the information a user most needs from an output-enable control. Artwork is
	`assets/indicator_{0,1}.png`, overridable per control via `on_pixmap=`/`off_pixmap=`.

	The lamp beside the button shows the BUTTON - it changes the instant the button is clicked, so
	the two can never disagree. What the instrument reports is the PV row's lamp (full view) and the
	value status lamp (both views); a click the instrument ignored shows up there as a mismatch.

	The two views label things differently, because in each one the button is the only element
	free to say something:
	  FULL:    the title carries the parameter name, so the button carries the STATE
	           ("Enabled"/"Disabled", or whatever on_text/off_text say), and the PV row is a
	           second indicator lamp showing what the instrument reports.
	  COMPACT: there is no title, so the button carries the NAME and the state lives entirely in
	           the indicator lamp beside it.
	'''

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str,
			set_args:callable=None, get_method:str=None, get_args:tuple=(),
			on_text:str="Enabled", off_text:str="Disabled", stale_after_s:float=5.0,
			view:str=ParameterView.COMPACT, edit_width:int=90,
			on_pixmap=None, off_pixmap=None, indicator_size:int=22):

		super().__init__(bridge, label, get, set_method, set_args, get_method, get_args,
			unit="", stale_after_s=stale_after_s, view=view, edit_width=edit_width)

		self.on_text = on_text
		self.off_text = off_text

		self.indicator = IndicatorLight(indicator_size, on_pixmap=on_pixmap, off_pixmap=off_pixmap)
		self.pv_indicator = IndicatorLight(indicator_size, on_pixmap=on_pixmap, off_pixmap=off_pixmap)

		self.button = QPushButton(self.off_text)
		self.button.setCheckable(True)
		self.button.setFixedWidth(edit_width)
		self.button.toggled.connect(self._on_toggled)

		self._build_layout(self.button, self.pv_indicator)

	def _extra_hidable(self):
		return (self.indicator,)

	def _add_sp_editor(self, sp_row):
		''' The SP row is [indicator][button]. The PV row is [indicator]. Both indicators are the
		same widget size in the same position after the SP/PV icon, so they line up in a column -
		which is what makes the two rows readable as "asked" above "actually". '''

		self.indicator.setVisible(True)
		sp_row.addWidget(self.indicator)
		sp_row.addWidget(self.button)
		self.button.setVisible(True)

	def show_inline_label(self) -> bool:
		# In compact the button itself carries the parameter name, so a separate label would say
		# it twice.
		return False

	def _button_text(self, checked:bool) -> str:

		if self.view == ParameterView.COMPACT:
			return self.label

		return self.on_text if checked else self.off_text

	def _on_toggled(self, checked):
		self.button.setText(self._button_text(checked))
		self.indicator.set_state(checked)
		if checked == self._setpoint:
			return
		self._user_changed(checked)

	def _format(self, value):
		if value is None:
			return ""
		return self.on_text if value else self.off_text

	def _display_setpoint(self, value):
		checked = bool(value) if value is not None else False
		if self.button.isChecked() != checked:
			self.button.blockSignals(True)
			self.button.setChecked(checked)
			self.button.blockSignals(False)
		self.button.setText(self._button_text(checked))
		self.indicator.set_state(self.button.isChecked())

	def _display_measured(self, value):
		self.pv_indicator.set_state(None if value is None else bool(value))

class ParameterChoice(_ParameterControlBase):
	''' An enumerated parameter. Worth a PV row in the full view: an instrument that silently
	refuses an unsupported mode looks identical to one that accepted it, until you can see what it
	actually reports. '''

	def __init__(self, bridge:InstrumentBridge, label:str, get:callable, set_method:str,
			choices:list, set_args:callable=None, get_method:str=None, get_args:tuple=(),
			labels:dict=None, stale_after_s:float=5.0, view:str=ParameterView.COMPACT,
			edit_width:int=90):

		self._labels = labels or {}

		super().__init__(bridge, label, get, set_method, set_args, get_method, get_args,
			unit="", stale_after_s=stale_after_s, view=view, edit_width=edit_width)

		self._choices = list(choices)

		self.combo = QComboBox()
		self.combo.addItems([self._format(c) for c in self._choices])
		self.combo.setFixedWidth(edit_width)
		self.combo.activated.connect(self._on_activated)

		self.pv_display = QLineEdit()
		self.pv_display.setReadOnly(True)
		self.pv_display.setFocusPolicy(Qt.FocusPolicy.NoFocus)
		self.pv_display.setFixedWidth(edit_width)

		self._build_layout(self.combo, self.pv_display)

	def _on_activated(self, index:int):
		value = self._choices[index]
		if value == self._setpoint:
			return
		self._user_changed(value)

	def _format(self, value):
		if value is None:
			return ""
		return self._labels.get(value, str(value))

	def _display_setpoint(self, value):
		if value is None or value not in self._choices:
			return
		idx = self._choices.index(value)
		if self.combo.currentIndex() != idx:
			self.combo.blockSignals(True)
			self.combo.setCurrentIndex(idx)
			self.combo.blockSignals(False)

	def _display_measured(self, value):
		self.pv_display.setText(self._format(value))

# ============================================================================
# Trace export - "save what is on the plot", in whichever format the next tool wants.
#
# Deliberately generic rather than oscilloscope-shaped: a trace is a labelled pair of x/y arrays,
# which is equally true of a VNA sweep or a spectrum. A category widget converts whatever it holds
# into `Trace` records and hands them here.
# ============================================================================

class Trace:
	''' One labelled curve, in whatever units the instrument reported. '''

	def __init__(self, label:str, x, y, x_unit:str="", y_unit:str="", metadata:dict=None):

		self.label = label
		self.x = list(x) if x is not None else []
		self.y = list(y) if y is not None else []
		self.x_unit = x_unit
		self.y_unit = y_unit
		self.metadata = metadata or {}

	def __len__(self):
		return len(self.y)

def _sanitize(name:str) -> str:
	return "".join(c if c.isalnum() or c in "-_." else "_" for c in str(name))

def _shared_x(traces) -> bool:
	''' Whether every trace sits on the same x axis, to within float noise.

	A scope's channels share a timebase, so a wide table (one time column, one column per channel)
	is what a person opening the CSV expects. Anything else has to be written long-form, because a
	wide table would silently imply a correspondence between rows that does not exist.
	'''

	if len(traces) < 2:
		return True

	first = traces[0].x
	for trace in traces[1:]:
		if len(trace.x) != len(first):
			return False
		if any(abs(a - b) > 1e-12 * max(1.0, abs(a)) for a, b in zip(trace.x, first)):
			return False

	return True

def export_csv(path:str, traces:list, figure=None, metadata:dict=None):
	''' Plain text, wide when the traces share an x axis and long when they do not. '''

	import csv

	with open(path, "w", newline="", encoding="utf-8") as f:

		writer = csv.writer(f)

		if _shared_x(traces) and traces:
			writer.writerow([f"x [{traces[0].x_unit}]"] + [f"{t.label} [{t.y_unit}]" for t in traces])
			for row in range(len(traces[0].x)):
				writer.writerow([traces[0].x[row]] + [t.y[row] if row < len(t.y) else "" for t in traces])
		else:
			writer.writerow(["trace", "x", "y"])
			for trace in traces:
				for x, y in zip(trace.x, trace.y):
					writer.writerow([trace.label, x, y])

def export_json(path:str, traces:list, figure=None, metadata:dict=None):
	''' Self-describing, and the easiest thing to read back in another language. '''

	import json

	document = {
		"format": "constellation-traces",
		"version": 1,
		"metadata": metadata or {},
		"traces": [{"label": t.label, "x": t.x, "y": t.y, "x_unit": t.x_unit,
			"y_unit": t.y_unit, "metadata": t.metadata} for t in traces],
	}

	with open(path, "w", encoding="utf-8") as f:
		json.dump(document, f, indent=1)

def export_png(path:str, traces:list, figure=None, metadata:dict=None):
	''' A picture of the plot as it currently looks. Data is not recoverable from it - that is the
	point of offering the other four alongside. '''

	if figure is None:
		raise ValueError("No figure to save - this panel has no plot.")

	figure.savefig(path, dpi=200, bbox_inches="tight")

def export_graf(path:str, traces:list, figure=None, metadata:dict=None):
	''' GrAF keeps the *figure* - styling, axes and data together - so a saved plot can be reopened
	and re-styled rather than just looked at. '''

	if figure is None:
		raise ValueError("No figure to save - this panel has no plot.")

	import graf

	graf.save_graf(figure, path, description=(metadata or {}).get("description", ""),
		conditions=metadata or {}, source_app="Constellation")

def export_tome(path:str, traces:list, figure=None, metadata:dict=None):
	''' TOME container.

	The writer is looked up at call time rather than imported at module scope, because no TOME
	implementation is published yet - see `_tome_writer()`.
	'''

	writer = _tome_writer()
	if writer is None:
		raise RuntimeError("No TOME writer is available - see _tome_writer() in ui.py.")

	document = {
		"metadata": metadata or {},
		"traces": {t.label: {"x": t.x, "y": t.y, "x_unit": t.x_unit, "y_unit": t.y_unit}
			for t in traces},
	}

	writer(document, path)

def _tome_writer():
	''' Finds a `dict -> .tome` writer, or None.

	**This is the one place to wire TOME up.** Nothing in the installed toolchain publishes a TOME
	writer today - `stardust.io` has `dict_to_hdf` but no TOME equivalent, and the only reference
	to `dict_to_tome` anywhere is an example in nebula's docstrings. Rather than guess at an API
	and produce files that a future TOME reader would reject, the format is offered and reports
	itself unavailable until one of these names exists.
	'''

	for module_name, attribute in (("tome", "dict_to_tome"), ("tome", "save_tome"),
			("stardust.io", "dict_to_tome"), ("jarnsaxa", "dict_to_tome")):

		try:
			import importlib
			module = importlib.import_module(module_name)
		except ImportError:
			continue

		writer = getattr(module, attribute, None)
		if writer is not None:
			return writer

	return None

def _graf_available() -> tuple:

	try:
		import graf   # noqa: F401
	except ImportError as e:
		return False, f"the `graf` package is not installed ({e})"

	return True, ""

def _tome_available() -> tuple:

	if _tome_writer() is None:
		return False, "no TOME writer is published yet - see _tome_writer() in ui.py"

	return True, ""

def _always_available() -> tuple:
	return True, ""

# key -> (menu label, extension, file-dialog filter, availability check, writer, needs_figure)
TRACE_EXPORTERS = {
	"tome": ("TOME", ".tome", "TOME container (*.tome)", _tome_available, export_tome, False),
	"graf": ("GrAF", ".graf", "GrAF figure (*.graf)", _graf_available, export_graf, True),
	"json": ("JSON", ".json", "JSON (*.json)", _always_available, export_json, False),
	"csv": ("CSV", ".csv", "Comma-separated values (*.csv)", _always_available, export_csv, False),
	"png": ("PNG", ".png", "PNG image (*.png)", _always_available, export_png, True),
}

TRACE_EXPORT_NOTES = {
	"tome": "Archive container, for putting the capture into a nebula session alongside its metadata.",
	"graf": "Keeps the whole figure - data, axes and styling - so it can be reopened and re-styled.",
	"json": "Self-describing text. The easiest format to read back from another language.",
	"csv": "Plain columns for a spreadsheet. One x column and one column per trace when they share an x axis.",
	"png": "A picture of the plot as it looks now. The numbers are not recoverable from it.",
}

class SaveTraceDialog(QDialog):
	''' Asks which format to save the captured traces in, then writes them.

	The formats are listed with what each one is *for* rather than just its extension, because the
	choice between "I want this in a spreadsheet" and "I want this back in a figure later" is the
	actual decision being made. A format whose writer is missing is shown disabled with the reason,
	rather than hidden - a silently absent option looks like the feature does not exist.
	'''

	def __init__(self, traces:list, figure=None, metadata:dict=None, parent=None, log:plf.LogPile=None):
		super().__init__(parent)

		self.traces = traces
		self.figure = figure
		self.metadata = metadata or {}
		self.log = log

		self.setWindowTitle("Save trace")
		self.setMinimumWidth(560)
		install_window_shortcuts(self, on_close=self.reject)

		layout = QVBoxLayout()

		points = sum(len(t) for t in traces)
		summary = QLabel(f"<b>{len(traces)} trace(s), {points} points</b><br>"
			+ ", ".join(t.label for t in traces) if traces else "<b>Nothing captured yet</b>")
		summary.setTextFormat(Qt.TextFormat.RichText)
		summary.setWordWrap(True)
		layout.addWidget(summary)

		self.buttons = {}

		# The radio carries the format name and the note lives in its own wrapping label beside
		# it: QRadioButton does not wrap its text, so a one-line-per-format layout silently clips
		# the explanation that is the whole reason the notes are there.
		grid = QGridLayout()
		grid.setColumnStretch(1, 1)

		first_enabled = None

		for row, (key, (label, _ext, _filter, available, _writer, needs_figure)) in enumerate(TRACE_EXPORTERS.items()):

			ok, reason = available()
			if ok and needs_figure and figure is None:
				ok, reason = False, "this panel has no plot to save"

			button = QRadioButton(label)
			button.setEnabled(ok)

			note = QLabel(TRACE_EXPORT_NOTES[key] if ok else f"Unavailable - {reason}")
			note.setWordWrap(True)
			note.setEnabled(ok)
			if not ok:
				button.setToolTip(f"Unavailable: {reason}")
				note.setToolTip(f"Unavailable: {reason}")

			grid.addWidget(button, row, 0, Qt.AlignmentFlag.AlignTop)
			grid.addWidget(note, row, 1)

			self.buttons[key] = button

			if ok and first_enabled is None:
				first_enabled = button

		if first_enabled is not None:
			first_enabled.setChecked(True)

		layout.addLayout(grid)

		box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
		box.accepted.connect(self.save)
		box.rejected.connect(self.reject)
		self.save_button = box.button(QDialogButtonBox.StandardButton.Save)
		self.save_button.setEnabled(bool(traces) and first_enabled is not None)
		layout.addWidget(box)

		self.setLayout(layout)

	def selected_format(self):

		for key, button in self.buttons.items():
			if button.isChecked():
				return key

		return None

	def save(self):

		key = self.selected_format()
		if key is None:
			return

		label, extension, file_filter, _available, writer, _needs_figure = TRACE_EXPORTERS[key]

		path, _ = QFileDialog.getSaveFileName(self, f"Save trace as {label}",
			f"trace{extension}", file_filter)

		if not path:
			return

		if not path.lower().endswith(extension):
			path += extension

		try:
			writer(path, self.traces, figure=self.figure, metadata=self.metadata)
		except Exception as e:
			if self.log is not None:
				self.log.error(f"Failed to save trace to >{path}<. ({e})")
			QMessageBox.critical(self, "Save failed", f"Could not write {path}:\n\n{e}")
			return

		if self.log is not None:
			self.log.info(f"Saved {len(self.traces)} trace(s) to >{path}<.")

		self.accept()

class ConnectionInfoDialog(QDialog):
	''' Where this panel's instrument actually is, and whether we can currently reach it.

	Static identity comes from the bridge (which knows its own addresses without touching a
	Driver); live status is fetched through `bridge.request("connection_summary")`, so nothing
	here reads driver state from the GUI thread while the worker thread is using it.
	'''

	def __init__(self, bridge, title:str="", parent=None):
		super().__init__(parent)

		self.bridge = bridge

		self.setWindowTitle(f"Connection info{' - ' + title if title else ''}")
		self.setMinimumWidth(480)
		install_window_shortcuts(self, on_close=self.reject)

		layout = QVBoxLayout()

		self.identity = QLabel()
		self.identity.setTextFormat(Qt.TextFormat.RichText)
		self.identity.setWordWrap(True)
		self.identity.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
		layout.addWidget(self.identity)

		line = QFrame()
		line.setFrameShape(QFrame.Shape.HLine)
		layout.addWidget(line)

		self.status = QLabel("Asking the instrument...")
		self.status.setTextFormat(Qt.TextFormat.RichText)
		self.status.setWordWrap(True)
		self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
		layout.addWidget(self.status)

		buttons = QHBoxLayout()
		refresh = QPushButton("Refresh")
		refresh.clicked.connect(self.refresh)
		buttons.addWidget(refresh)
		buttons.addStretch(1)

		box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
		box.rejected.connect(self.reject)
		buttons.addWidget(box)
		layout.addLayout(buttons)

		self.setLayout(layout)

		bridge.command_result.connect(self._on_result)
		bridge.connection_changed.connect(lambda online: self.refresh())

		self._render_identity()
		self.refresh()

	def _render_identity(self):

		rows = []
		for key, value in (self.bridge.describe() or {}).items():
			rows.append(f"<b>{_html(key)}:</b> {_html(value)}")

		self.identity.setText("<br>".join(rows) or "<i>This bridge reports no addressing information.</i>")

	def refresh(self):
		self.bridge.request("connection_summary")

	def _on_result(self, method_name, args, success, result):

		if method_name != "connection_summary":
			return

		if not success:
			self.status.setText(f"<b>Status:</b> could not be read<br><span style='color:#e74c3c'>{_html(result)}</span>")
			return

		if not isinstance(result, dict):
			self.status.setText(f"<b>Status:</b> {_html(result)}")
			return

		rows = [f"<b>{_html(key)}:</b> {_html(value)}" for key, value in result.items()]
		self.status.setText("<br>".join(rows))

# ============================================================================
# Category -> widget registration, so ConstellationWindow.add_instrument(driver) works without
# the caller needing to know which widget class handles that driver's category.
# ============================================================================

class SyncConfigDialog(QDialog):
	''' Per instrument: whether its state is polled, and whether the panel's values are re-sent.

	Polling on + auto-send off is monitoring: the panel follows the instrument and writes to it
	only when the user changes something. Changes apply immediately.
	'''

	def __init__(self, window):
		super().__init__(window)

		self.setWindowTitle("Instrument Sync")
		self.rows = {}   # panel title -> dict of this instrument's widgets
		self._main_window = window

		layout = QVBoxLayout()

		if not window.instrument_widgets:
			layout.addWidget(QLabel("No instruments connected."))

		for widget in window.instrument_widgets:
			title = getattr(widget, "panel_title", None) or type(widget).__name__
			layout.addWidget(self._instrument_group(title, widget))

		close = QPushButton("Close")
		close.clicked.connect(self.accept)

		buttons = QHBoxLayout()
		buttons.addStretch(1)
		buttons.addWidget(close)
		layout.addLayout(buttons)

		self.setLayout(layout)
		install_window_shortcuts(self, on_close=self.reject)

	def _instrument_group(self, title:str, widget) -> QGroupBox:

		bridge = widget.bridge

		group = QGroupBox(title)
		grid = QGridLayout()

		poll_check = QCheckBox("Poll instrument")
		poll_check.setToolTip("Read the instrument's state periodically and update the panel.")
		poll_period = self._period_edit(bridge.poll_interval_s)

		send_check = QCheckBox("Auto-send panel values")
		send_check.setToolTip("Periodically re-send the values you have set on the panel. "
			"Off: the instrument is written only when you change something.")
		send_period = self._period_edit(widget.auto_send_interval_s)

		if bridge.supports_polling:
			poll_check.setChecked(bool(bridge.poll_enabled))
		else:
			reason = "Updates arrive from the process that owns this instrument."
			for w in (poll_check, poll_period):
				w.setEnabled(False)
				w.setToolTip(reason)

		send_check.setChecked(widget.auto_send_enabled)

		def apply_poll(*_):
			try:
				bridge.set_polling(poll_check.isChecked(), float(poll_period.text()))
			except (ValueError, NotImplementedError):
				poll_period.setText(_format_period(bridge.poll_interval_s))
				return
			self._save(widget)

		def apply_send(*_):
			try:
				widget.set_auto_send(send_check.isChecked(), float(send_period.text()))
			except ValueError:
				send_period.setText(_format_period(widget.auto_send_interval_s))
				return
			self._save(widget)

		poll_check.toggled.connect(apply_poll)
		poll_period.editingFinished.connect(apply_poll)
		send_check.toggled.connect(apply_send)
		send_period.editingFinished.connect(apply_send)

		for row, (check, period) in enumerate(((poll_check, poll_period), (send_check, send_period))):
			grid.addWidget(check, row, 0)
			grid.addWidget(QLabel("every"), row, 1)
			grid.addWidget(period, row, 2)
			grid.addWidget(QLabel("s"), row, 3)

		grid.setColumnStretch(4, 1)
		group.setLayout(grid)

		self.rows[title] = {"poll_check": poll_check, "poll_period": poll_period,
			"send_check": send_check, "send_period": send_period}

		return group

	def _save(self, widget):
		save = getattr(self._main_window, "save_sync_settings", None)
		if save is not None:
			save(widget)

	@staticmethod
	def _period_edit(value) -> QLineEdit:

		edit = QLineEdit(_format_period(value))
		validator = QDoubleValidator(0.05, 3600.0, 3)
		validator.setLocale(_scpi_locale())
		edit.setValidator(validator)
		edit.setFixedWidth(60)

		return edit

def _format_period(value) -> str:
	return "" if value is None else f"{float(value):g}"

def add_view_arguments(parser):
	''' Adds --full / --compact to an argparse parser, for the density every control starts in.
	Pass the result of view_from_arguments() as ConstellationWindow(parameter_view=...). '''

	group = parser.add_mutually_exclusive_group()
	group.add_argument("--full", dest="parameter_view", action="store_const", const=ParameterView.FULL,
		help="Show every control in the full view (title, setpoint and measured rows, three lamps).")
	group.add_argument("--compact", dest="parameter_view", action="store_const", const=ParameterView.COMPACT,
		help="Show every control in the compact view (one row, two lamps).")

	return parser

def view_from_arguments(args):
	''' The density chosen by add_view_arguments()'s flags, or None if neither was given. '''
	return getattr(args, "parameter_view", None)

# Setting this environment variable to a file path makes Constellation keep its settings in that INI
# file instead of the platform's usual place. The test suite uses it so a test run can never read or
# overwrite a real user's settings.
SETTINGS_FILE_ENV = "CONSTELLATION_SETTINGS_FILE"

def default_settings():
	''' The QSettings Constellation remembers things in. '''

	path = os.environ.get(SETTINGS_FILE_ENV)
	if path:
		return QtCore.QSettings(path, QtCore.QSettings.Format.IniFormat)

	return QtCore.QSettings("Constellation", "Constellation")

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

	def __init__(self, log:plf.LogPile, add_menu:bool=True, settings=None, parameter_view:str=None):
		super().__init__()
		self.log = log

		if parameter_view is not None and parameter_view not in ParameterView.ORDER:
			raise ValueError(f"Unknown view >{parameter_view}<")

		# Density every Parameter* control in this window starts in - e.g. from --full/--compact
		# (see add_view_arguments). None leaves each control as its widget built it.
		self.parameter_view = parameter_view

		# Where per-instrument settings (polling, auto-send) are remembered between runs.
		self.settings = settings if settings is not None else default_settings()

		self.instrument_widgets = []
		self._bridges = []

		self.setDockNestingEnabled(True)

		# Cmd/Ctrl-W and Cmd/Ctrl-Q, on every Constellation window. Installed here rather than
		# relying on the menu bar alone, because the menu is optional (`add_menu=False`) and on
		# some platforms a menu action's shortcut is not active until the menu is shown.
		self._window_shortcuts = install_window_shortcuts(self)

		if add_menu:
			self.add_basic_menu_bar()

		self._sync_dialog = None
		self._build_status_bar()

	def _build_status_bar(self):
		''' A status bar on every Constellation window, independent of the menu bar. For now it
		holds the Config button; more status goes here later. '''

		self.status_bar = self.statusBar()

		self.config_button = QToolButton()
		self.config_button.setText("Config")
		self.config_button.setAutoRaise(True)
		self.config_button.setToolTip("Polling and auto-send settings")
		self.config_button.clicked.connect(self.show_sync_config)

		self.status_bar.addPermanentWidget(self.config_button)

	def set_parameter_view(self, view:str):
		''' Switches every control on every panel to `view`, and makes it the default for controls
		and panels added afterwards. '''

		if view not in ParameterView.ORDER:
			raise ValueError(f"Unknown view >{view}<")

		self.parameter_view = view

		for widget in self.instrument_widgets:
			widget.set_parameter_view(view)

	def _sync_group(self, widget):

		key = widget.bridge.settings_key()
		if key is None:
			return None

		# "/" separates groups in QSettings, and a VISA resource or labmesh id may contain one.
		return "sync/" + key.replace("/", "_").replace("\\", "_")

	def restore_sync_settings(self, widget):
		''' Applies an instrument's saved polling and auto-send settings to its panel, if any were
		saved. A saved value that no longer makes sense is ignored rather than raised. '''

		group = self._sync_group(widget)
		if group is None:
			return

		s = self.settings
		bridge = widget.bridge

		if bridge.supports_polling and s.contains(f"{group}/poll_enabled"):
			try:
				bridge.set_polling(s.value(f"{group}/poll_enabled", type=bool),
					s.value(f"{group}/poll_interval_s", bridge.poll_interval_s, type=float))
			except (ValueError, TypeError):
				pass

		if s.contains(f"{group}/auto_send_enabled"):
			try:
				widget.set_auto_send(s.value(f"{group}/auto_send_enabled", type=bool),
					s.value(f"{group}/auto_send_interval_s", widget.auto_send_interval_s, type=float))
			except (ValueError, TypeError):
				pass

	def save_sync_settings(self, widget):
		''' Remembers an instrument's current polling and auto-send settings. '''

		group = self._sync_group(widget)
		if group is None:
			return

		s = self.settings
		bridge = widget.bridge

		if bridge.supports_polling:
			s.setValue(f"{group}/poll_enabled", bool(bridge.poll_enabled))
			s.setValue(f"{group}/poll_interval_s", float(bridge.poll_interval_s))

		s.setValue(f"{group}/auto_send_enabled", bool(widget.auto_send_enabled))
		s.setValue(f"{group}/auto_send_interval_s", float(widget.auto_send_interval_s))
		s.sync()

	def show_sync_config(self):
		''' Opens (or raises) the polling/auto-send settings window. Rebuilt each time it is opened,
		so it always lists the instruments currently docked. '''

		if self._sync_dialog is not None:
			self._sync_dialog.close()

		self._sync_dialog = SyncConfigDialog(self)
		self._sync_dialog.show()
		self._sync_dialog.raise_()
		self._sync_dialog.activateWindow()

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
		widget.panel_title = panel_title

		# Before the bridge starts, so a saved "don't poll" is honoured from the first moment
		# rather than after one unwanted poll.
		self.restore_sync_settings(widget)

		bridge.start()
		self._bridges.append(bridge)

		self._rebuild_instrument_menu()

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
		self.close_window_act.setShortcut(QKeySequence.StandardKey.Close)
		self.close_window_act.triggered.connect(self._basic_menu_close)
		self.file_menu.addAction(self.close_window_act)

		self.view_log_act = QAction("View Log", self)
		self.view_log_act.setShortcut("Shift+L")
		self.view_log_act.triggered.connect(self._basic_menu_view_log)
		self.file_menu.addAction(self.view_log_act)

		self.file_menu.addSeparator()

		self.quit_act = QAction("Quit", self)
		self.quit_act.setShortcut(QKeySequence.StandardKey.Quit)
		self.quit_act.setMenuRole(QAction.MenuRole.QuitRole)
		self.quit_act.triggered.connect(QApplication.quit)
		self.file_menu.addAction(self.quit_act)

		#----------------- Instrument Menu ----------------

		self.instrument_menu = self.bar.addMenu("Instrument")

		#----------------- View Menu ----------------

		# Display preferences a panel offers (e.g. how it arranges its channels). Kept out of the
		# panel itself so the controls stay the most prominent thing on it.
		self.view_menu = self.bar.addMenu("View")

		self._rebuild_instrument_menu()

	def _rebuild_instrument_menu(self):
		''' Rebuilds the Instrument menu from the panels currently docked.

		With one instrument the actions sit directly in the menu; with several, each gets its own
		submenu. Naming them is the point - "Refresh state" is ambiguous the moment a second
		instrument is on screen.
		'''

		menu = getattr(self, "instrument_menu", None)
		if menu is None:
			return

		menu.clear()

		panels = [(getattr(w, "panel_title", None) or type(w).__name__, w)
			for w in self.instrument_widgets]

		# Before the early return below: the View menu needs its "nothing here" placeholder too,
		# or with no instruments docked it is left as an empty menu that looks broken.
		self._rebuild_view_menu(panels)

		if not panels:
			placeholder = menu.addAction("No instruments connected")
			placeholder.setEnabled(False)
			return

		for title, widget in panels:
			target = menu if len(panels) == 1 else menu.addMenu(title)
			self._add_instrument_actions(target, widget, title)

	def _rebuild_view_menu(self, panels:list):
		''' Rebuilds the View menu: control density for the whole window first, then what each
		docked panel offers via add_view_actions().

		Panel options follow the Instrument menu's shape: flat for one panel, a submenu per panel for
		several - and with several, each submenu also offers density for just that panel.
		'''

		menu = getattr(self, "view_menu", None)
		if menu is None:
			return

		menu.clear()

		for view in (ParameterView.FULL, ParameterView.COMPACT):
			action = QAction(f"All Controls: {ParameterView.LABELS[view]}", self)
			action.triggered.connect(lambda checked=False, view=view: self.set_parameter_view(view))
			menu.addAction(action)

		if len(panels) == 1:
			title, widget = panels[0]
			if widget.has_view_actions():
				menu.addSeparator()
				widget.add_view_actions(menu)
			return

		if panels:
			menu.addSeparator()

		for title, widget in panels:

			sub = menu.addMenu(title)

			for view in (ParameterView.FULL, ParameterView.COMPACT):
				action = QAction(f"Controls: {ParameterView.LABELS[view]}", self)
				action.triggered.connect(lambda checked=False, widget=widget, view=view: widget.set_parameter_view(view))
				sub.addAction(action)

			if widget.has_view_actions():
				sub.addSeparator()
				widget.add_view_actions(sub)

	def _add_instrument_actions(self, menu, widget, title:str):

		bridge = widget.bridge

		def add(text, slot, shortcut=None):
			action = QAction(text, self)
			if shortcut:
				action.setShortcut(shortcut)
			action.triggered.connect(slot)
			menu.addAction(action)
			return action

		# Everything goes through bridge.request(), never a Driver call from the GUI thread - a
		# state refresh can take seconds on a real instrument, and blocking here would freeze
		# every other panel in the window.
		add("Refresh State", lambda: bridge.request("refresh_state"))
		add("Apply State", lambda: bridge.request("apply_state"))

		menu.addSeparator()

		add("Save State...", lambda: self._save_instrument_state(bridge, title))
		add("Load State...", lambda: self._load_instrument_state(bridge, title))

		menu.addSeparator()

		add("Get Connection Info...", lambda: self._show_connection_info(bridge, title))

	def _save_instrument_state(self, bridge, title:str):

		path, _ = QFileDialog.getSaveFileName(self, f"Save state - {title}", "instrument.state.hdf",
			"Instrument state (*.hdf *.state.hdf)")

		if not path:
			return

		bridge.request("dump_state", path)
		self.log.info(f"Saving instrument state to >{path}<.")

	def _load_instrument_state(self, bridge, title:str):

		path, _ = QFileDialog.getOpenFileName(self, f"Load state - {title}", "",
			"Instrument state (*.hdf *.state.hdf)")

		if not path:
			return

		bridge.request("restore_state", path)
		self.log.info(f"Loading instrument state from >{path}<. Use Apply State to send it to the instrument.")

		# restore_state() only refills the Driver's own state object - it deliberately does not
		# touch the instrument. Saying so here beats a user wondering why the hardware did not
		# move.
		QMessageBox.information(self, "State loaded",
			"The state was loaded into the driver.\n\nIt has NOT been sent to the instrument - "
			"use Instrument > Apply State to do that.")

	def _show_connection_info(self, bridge, title:str):

		dialog = ConnectionInfoDialog(bridge, title=title, parent=self)
		dialog.show()

		# Held on the window so it is not garbage collected the moment this method returns.
		if not hasattr(self, "_info_dialogs"):
			self._info_dialogs = []
		self._info_dialogs.append(dialog)
		dialog.finished.connect(lambda _r, d=dialog: self._info_dialogs.remove(d))

	def _basic_menu_close(self):
		# Closes this window only. This used to call sys.exit(0) straight after close(), so
		# "Close Window" killed the whole application and took every other instrument's panel
		# with it - which is what Quit is for, and it is now a separate action.
		self.close()

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

		# Off by default: a panel writes to its instrument only when the user changes something,
		# which is what makes a panel safe to open just to watch an instrument. See set_auto_send().
		self.auto_send_enabled = False
		self.auto_send_interval_s = 5.0
		self._auto_send_timer = QTimer(self)
		self._auto_send_timer.timeout.connect(self.push_setpoints)

		# Density every control on this panel should start in, if one was asked for (a window-wide
		# --full/--compact, or a View-menu choice). None leaves each control as its widget built it.
		self.parameter_view = None

		bridge.state_changed.connect(self.on_state_changed)
		# Connected AFTER on_state_changed on purpose: slots run in connection order, so this sees
		# any controls a widget builds lazily from its first state update (per-channel controls).
		bridge.state_changed.connect(self._apply_default_view)
		bridge.connection_changed.connect(self.on_connection_changed)

		self.main_window.instrument_widgets.append(self)

	def default_parameter_view(self):
		''' The density new controls on this panel should take: the panel's own, else the window's. '''
		return self.parameter_view or getattr(self.main_window, "parameter_view", None)

	def _apply_default_view(self, state=None):
		''' Gives every control that has not yet had it the default density. Once per control, so a
		control the user switched by hand is never switched back by the next poll. '''

		view = self.default_parameter_view()
		if view is None:
			return

		for control in self.parameter_controls():
			if not getattr(control, "_default_view_applied", False):
				control.set_view(view)
				control._default_view_applied = True

	def set_parameter_view(self, view:str):
		''' Switches every control on this panel to `view`, including ones built later. '''

		if view not in ParameterView.ORDER:
			raise ValueError(f"Unknown view >{view}<")

		self.parameter_view = view

		for control in self.parameter_controls():
			control.set_view(view)
			control._default_view_applied = True

	def parameter_controls(self) -> list:
		''' Every Parameter* control on this panel, wherever it is nested. '''
		return self.findChildren(_ParameterControlBase)

	def push_setpoints(self) -> int:
		''' Re-sends every value the user has set on this panel. Controls that have only ever
		mirrored the instrument are left alone. Returns how many were sent. '''

		sent = 0

		for control in self.parameter_controls():
			if control.user_setpoint is not None:
				control.resend()
				sent += 1

		return sent

	def set_auto_send(self, enabled:bool, interval_s:float=None):
		''' Periodically re-sends this panel's user-set values to the instrument, so the instrument
		is held at what the panel says even if something else changes it.

		Raises:
			ValueError: If `interval_s` is not a positive number.
		'''

		if interval_s is not None:
			interval_s = float(interval_s)
			if interval_s <= 0:
				raise ValueError(f"Auto-send interval must be positive, got {interval_s}.")
			self.auto_send_interval_s = interval_s

		self.auto_send_enabled = bool(enabled)

		if self.auto_send_enabled:
			self._auto_send_timer.start(int(self.auto_send_interval_s * 1000))
		else:
			self._auto_send_timer.stop()

	def on_state_changed(self, state):
		''' Optional hook for anything a widget needs beyond what its Tracked* controls already
		handle automatically (e.g. redrawing a waveform plot). Default no-op - most of a category
		widget's per-field updating should come from Tracked* controls, not this. '''
		pass

	def has_view_actions(self) -> bool:
		''' Whether this panel contributes anything to the window's View menu. Override together
		with add_view_actions(). '''
		return False

	def add_view_actions(self, menu) -> None:
		''' Adds this panel's display options to `menu` (the window's View menu, or this panel's
		submenu of it when several instruments are docked).

		Display preferences belong here rather than as controls on the panel, so the instrument's
		own controls stay the most prominent thing on screen. Called again every time the menu is
		rebuilt, so create fresh actions each call and reflect current state when doing so.
		'''
		pass

	def on_connection_changed(self, online:bool):
		''' Optional hook, e.g. to grey out the whole panel while offline. Default no-op. '''
		pass
