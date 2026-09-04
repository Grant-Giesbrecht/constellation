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
	QDialogButtonBox, QSizePolicy, QFrame, QCheckBox, QLCDNumber)
from PyQt6.QtGui import QAction

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
			self._record_scpi(method_name)
			self.command_result.emit(method_name, args, True, result)
		except Exception as e:
			self._record_scpi(method_name)
			self.command_result.emit(method_name, args, False, e)

		# The command may have changed instrument state - refresh and push it right away rather
		# than waiting for the next scheduled poll.
		self._poll_and_emit()

	def _record_scpi(self, method_name:str):
		''' Notes the SCPI the driver just sent, attributed to the method that sent it. '''

		relay = getattr(self.driver, "relay", None)
		if relay is None:
			return

		command = getattr(relay, "last_command", None)
		if command is not None:
			self.last_scpi[method_name] = (command, getattr(relay, "last_response", None))

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
		FULL: "Full - setpoint, measured value, three lamps",
		COMPACT: "Compact - setpoint only, two lamps",
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

# SI prefixes offered by the unit selector, largest first. `µ` is spelled with the MICRO SIGN so it
# renders on every platform without a font that has GREEK SMALL LETTER MU.
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

		self.lcd_check = QCheckBox("Show the measured value on an LCD readout (full view only)")
		self.lcd_check.setChecked(bool(getattr(control, "lcd", False)))
		self.lcd_check.setEnabled(getattr(control, "supports_lcd", False))
		self.lcd_check.toggled.connect(control.set_lcd)
		layout.addWidget(self.lcd_check)

		layout.addWidget(self._separator())

		# --- the three lamps, spelled out ---
		self.lamp_rows = {}
		grid = QGridLayout()
		grid.setColumnStretch(2, 1)

		for row, (key, title) in enumerate((
				("verification", "Verification"),
				("send", "Setpoint sent"),
				("value", "Measured value"))):

			lamp = StatusLamp(13)
			lamp.setCursor(QtGui.QCursor(Qt.CursorShape.ArrowCursor))
			name = QLabel(f"<b>{title}</b>")
			name.setTextFormat(Qt.TextFormat.RichText)
			text = QLabel()
			text.setWordWrap(True)

			grid.addWidget(lamp, row, 0)
			grid.addWidget(name, row, 1)
			grid.addWidget(text, row, 2)

			self.lamp_rows[key] = (lamp, text)

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

		self._set_row("verification", VERIFICATION_COLORS.get(verification_key, "#888888"),
			f"<b>{verification_key}</b> - {VERIFICATION_TEXT.get(verification_key, '')}<br>"
			+ "<br>".join(control.verification_lines))

		self._set_row("send", SEND_COLORS[send_key], f"<b>{send_key}</b> - {SEND_TEXT[send_key]}"
			+ (f"<br><span style='color:#e74c3c'>{control.send_error}</span>" if control.send_error else ""))

		self._set_row("value", VALUE_COLORS[value_key], f"<b>{value_key}</b> - {VALUE_TEXT[value_key]}"
			+ (f"<br><span style='color:#e74c3c'>{control.query_error}</span>" if control.query_error else ""))

		self.resend_button.setEnabled(control.setpoint is not None and verification_key != "unavailable")

		if self.view_combo.currentIndex() != ParameterView.ORDER.index(control.view):
			self.view_combo.setCurrentIndex(ParameterView.ORDER.index(control.view))

		command, response = control.last_scpi()

		rows = [
			f"<b>Last sent:</b> {_html(control.setpoint)}",
			f"<b>Last received:</b> {_html(control.confirmed)}",
			f"<b>Driver call:</b> <code>{_html(control.call_signature())}</code>",
		]

		if command is not None:
			rows.append(f"<b>SCPI sent:</b> <code>{_html(command)}</code>")
			if response is not None:
				rows.append(f"<b>SCPI reply:</b> <code>{_html(response)}</code>")
		else:
			# Not an error - a dummy driver never touches a relay, and an ObserverBridge issues
			# the call in another process entirely, so there is no local SCPI to show.
			rows.append("<span style='color:gray'>No SCPI recorded for this parameter yet.</span>")

		self.traffic.setText("<br>".join(rows))

	def _set_row(self, key:str, color:str, text:str):
		lamp, label = self.lamp_rows[key]
		lamp.set(color, "")
		label.setText(text)

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

		# Rebuilding the layout must not take the widgets with it. Two traps here, both of which
		# manifest as "wrapped C/C++ object ... has been deleted" on the next value update:
		#
		#  - emptying the layout in place and reparenting its SUBLAYOUTS to None destroys widgets
		#    the current view had not placed (an unused LCD has no other owner);
		#  - handing a still-populated layout to a throwaway widget re-parents everything it
		#    manages onto that widget, which then dies with it.
		#
		# So: drain every item out first (widgets stay children of this control), pin ownership
		# explicitly, and only then dispose of the empty husk.
		old_layout = self.layout()
		if old_layout is not None:
			_drain_layout(old_layout)

		for widget in self._managed_widgets():
			widget.setParent(self)
			widget.setVisible(False)

		if old_layout is not None:
			QWidget().setLayout(old_layout)

		self._root = QVBoxLayout()
		self._root.setContentsMargins(4, 2, 4, 2)
		self._root.setSpacing(2)
		self.setLayout(self._root)

		# The title sits ABOVE the row-plus-lamps block rather than inside it, so the lamp column
		# centres on the fields it annotates. Centring it over the title as well pushed the lamps
		# up out of line with the rows whose status they report.
		if full and self.show_title():
			self.title_label.setVisible(True)
			self._root.addWidget(self.title_label)

		rows = QVBoxLayout()
		rows.setSpacing(2)

		sp_row = QHBoxLayout()
		sp_row.setSpacing(4)

		if self.show_inline_label():
			self.inline_label.setVisible(True)
			sp_row.addWidget(self.inline_label)

		if self.show_sp_icon():
			self.sp_icon.setVisible(True)
			sp_row.addWidget(self.sp_icon)

		self._add_sp_editor(sp_row)

		# The prefix selector sits immediately right of the setpoint field, and doubles as the
		# unit label - so there is exactly one place the units are stated for the SP row.
		if self.has_prefixes:
			self.prefix_combo.setVisible(True)
			sp_row.addWidget(self.prefix_combo)
		elif self.unit:
			self.unit_sp.setVisible(True)
			sp_row.addWidget(self.unit_sp)

		# Both rows get a trailing stretch so both pack LEFT. Without it the SP row spread its
		# slack between its widgets while the PV row (which has one) stayed put, and the two rows'
		# indicators drifted out of column - which is exactly the alignment that makes an SP row
		# above a PV row readable as "asked" above "actually".
		sp_row.addStretch(1)
		rows.addLayout(sp_row)

		if full and self.show_pv_row():
			pv_row = QHBoxLayout()
			pv_row.setSpacing(4)
			self.pv_icon.setVisible(True)
			pv_row.addWidget(self.pv_icon)

			pv = self._pv_row_widget()
			pv.setVisible(True)
			pv_row.addWidget(pv)

			# The PV row's unit label mirrors the selector, so both rows always read in the same
			# units - which is the entire point of scaling them together.
			if self.has_prefixes or self.unit:
				self.unit_pv.setText(self.prefix_combo.currentText() if self.has_prefixes else self.unit)
				self.unit_pv.setVisible(True)
				pv_row.addWidget(self.unit_pv)

			pv_row.addStretch(1)
			rows.addLayout(pv_row)

		# Stretch on BOTH sides centres the lamp column against the rows it annotates. With a
		# stretch only underneath, the lamps rode the top of whatever cell the control was placed
		# in and drifted away from the control they describe as soon as the row got taller.
		lamps = QVBoxLayout()
		lamps.setSpacing(3)
		lamps.addStretch(1)

		for lamp in self.visible_lamps():
			lamp.setVisible(True)
			lamps.addWidget(lamp)

		lamps.addStretch(1)

		body = QHBoxLayout()
		body.setSpacing(6)
		body.addLayout(rows)
		body.addLayout(lamps)

		self._root.addLayout(body)

		# Any height beyond what the rows need pools here rather than being shared out between
		# them. The size policy above already stops a well-behaved container from growing the
		# widget at all; this keeps the rows packed even when something resizes it directly.
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

	def _user_changed(self, new_value):
		self._send_state = "unsent"
		self.send_error = ""
		self._awaiting_readback = True
		self._dirty = False
		super()._user_changed(new_value)

	def _on_command_result(self, method_name, args, success, result):

		if method_name == self.set_method:
			self._send_state = "sent" if success else "failed"
			self.send_error = "" if success else str(result)

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
			f"Measured: {value}\n{VALUE_TEXT[value]}\nmeasured = {self._confirmed}"
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


def _drain_layout(layout):
	''' Removes every item from a layout tree without disturbing any widget's parent.

	takeAt() detaches a QWidgetItem but leaves the widget itself parented where it was, which is
	exactly what a layout rebuild needs: the control keeps its widgets, the layout keeps nothing.
	'''

	while layout.count():

		item = layout.takeAt(0)

		child = item.layout()
		if child is not None:
			_drain_layout(child)

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
			auto_prefix:bool=True, lcd:bool=False, lcd_digits:int=6):

		super().__init__(bridge, label, get, set_method, set_args, get_method, get_args, unit,
			tolerance, abs_tolerance, stale_after_s, view, edit_width, prefixes, auto_prefix)

		self.supports_lcd = True
		self.lcd = bool(lcd)

		self.edit = QLineEdit()
		if validator is not None:
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

	Both lamps follow the *instrument*, not the button: they show what was last read back, so a
	button that was clicked and did nothing is visible rather than inferred.

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

	def _display_measured(self, value):
		state = None if value is None else bool(value)
		self.indicator.set_state(state)
		self.pv_indicator.set_state(state)

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
