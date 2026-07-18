# GUI architecture proposal

Goal: instrument GUIs that work out of the box from a driver + address (local or networked), can
be paneled together into one window, never freeze because of another instrument, always make
setpoint-vs-actual state discernible, and are easy for a future collaborator to add or extend.

This is a proposal, not an implementation - see "Open decisions" at the end before any of this
gets built.

## Diagnosis of the current prototype (`ui.py` / `oscilloscope_gui.py`)

- **Freezes**: `OscilloscopeWidget.state_to_ui()` calls `driver.get_waveform(i)` directly on the
  Qt GUI thread, once per channel. A single `get_waveform()` call can legitimately take several
  seconds (see the RigolDS1000Z waveform-capture work). Since there is one Qt event loop for the
  whole window, this freezes every panel, not just the oscilloscope's - and it only happens on a
  manual "Refresh UI from State" menu action; there's no live/periodic update at all yet.
- **Not actually composable yet**: `ConstellationWindow` builds a `QGridLayout` in `__init__` and
  never uses it. `instrument_widgets` is a flat list used only to broadcast a manual refresh, not
  a real panel-arrangement mechanism. Each `InstrumentWidget` *is* already a self-contained
  `QWidget`, which is the right shape - the container side just doesn't do anything with that yet.
- **No setpoint-vs-actual concept**: `driver.state` holds a single value per field - "what we
  asked for" and "what's confirmed" are conflated (this is also flagged in
  `docs/dummy_and_state_review.md`). The widgets don't yet wire user input back to the driver at
  all (`ChannelWidget`'s `editingFinished.connect(...)` is commented out).
- **No registration/extension pattern**: nothing links a category to its widget class, and every
  control was hand-built with no reusable "setpoint + confirmed value + indicator" component.
- **Already right**: `OscilloscopeWidget`/`ChannelWidget` take a generic `driver:Driver` and only
  touch category-level state/methods, never anything Rigol-specific. That's the correct instinct -
  one widget per *category*, free for every driver in it - just not yet formalized.
- **Packaging gap**: `PyQt6`/`matplotlib` are imported but not declared in `pyproject.toml` at all.

## Architecture

Three new pieces, layered on top of what already exists (`Driver`/`CommandRelay`/`InstrumentState`
untouched):

### 1. `InstrumentBridge` - decouples the GUI thread from instrument I/O

A plain `QObject` (not a widget - nothing visual), one per instrument, that owns all interaction
with a `Driver` and is the *only* thing allowed to touch `driver`/`driver.state`. Two backings,
same signal interface, so widgets never know or care which one they're connected to:

- **`OwningBridge(driver)`**: holds a real `Driver` instance and a dedicated `threading.Thread`
  running a command queue. `bridge.request(driver.set_div_volt, 1, 2.0)` enqueues the call and
  returns immediately; the worker thread executes it, whatever it was talking to (a local
  `DirectSCPIRelay` or a networked `RemoteTextCommandRelayClient` - the bridge doesn't care, same
  as `Driver` already doesn't care). The same thread also periodically calls `driver.poll()`
  (already exists, already does `refresh_state()` + `state_to_dict()`) and emits the result.
- **`ObserverBridge(relay_id, broker_...)`**: no `Driver` at all - subscribes to a
  `DriverStateBroadcaster`'s PUB feed via `DirectorClientAgent.on_state(...)` (both already exist
  from the labmesh work), running labmesh's asyncio client on its own thread, translating each
  push into the same signals an `OwningBridge` would emit. This is what makes "GUI running
  alongside an automatic script" work: the script's process runs the `OwningBridge`, the GUI's
  process runs an `ObserverBridge` pointed at the same `relay_id` - no code difference in the
  widget either way.

Signals (indicative, not final):
```python
class InstrumentBridge(QObject):
    state_changed = pyqtSignal(dict)                # confirmed state snapshot (from poll/broadcast)
    command_result = pyqtSignal(str, tuple, bool, object)  # method name, args, success, value/error
    connection_changed = pyqtSignal(bool)            # online/offline
```

Threads doing I/O release the GIL while blocked on network/serial reads, so plain `threading.Thread`
is sufficient here (this is I/O-bound work, not CPU-bound) - no need for the complexity of separate
processes. Qt's signal/slot mechanism is already cross-thread-safe when a signal is emitted from a
worker thread and connected to a slot in the GUI thread (`Qt.ConnectionType.QueuedConnection`,
selected automatically), so this is standard, well-trodden Qt practice, not a novel mechanism.

**Hard rule for widget authors: never touch `bridge.driver` directly.** Only go through
`bridge.request(...)` and the `state_changed`/`command_result` signals. This is what actually
prevents both the freezing (nothing on the GUI thread blocks) and data races (the driver's state
is only ever touched from its own bridge's worker thread).

### 2. `TrackedControl` - the reusable setpoint-vs-actual building block

A small wrapper widget composed around each individual control (a value entry, a toggle button,
a dropdown) that a category widget author uses instead of hand-wiring a `QLineEdit`/`QPushButton`
directly:

```python
control = TrackedControl(
    bridge, label="Volts/div",
    get=lambda state: state["channels"][ch]["div_volt"],
    set=lambda v: bridge.request(driver.set_div_volt, ch, v),
    widget_factory=make_line_edit,  # or make_toggle_button, make_dropdown, ...
)
```

It owns exactly one visual indicator convention, used identically across every category so a
collaborator only has to learn it once:

| State | Meaning | Indicator |
|---|---|---|
| confirmed | last `state_changed` matches what the user last set | neutral/normal |
| pending | user changed it, waiting on `command_result` | amber, e.g. a small spinner or dashed border |
| mismatch | confirmed value differs from what was last requested (write failed, or something else changed it) | red, with both values visible on hover/tooltip - this is your literal example: asked for channel 1 on, scope reports off |
| stale | no `state_changed` received recently (bridge offline, or instrument stopped responding) | grey-out + "last seen Ns ago" |

This directly satisfies the discernibility requirement, and turns "build a new category's GUI"
into "assemble `TrackedControl`s for each parameter," rather than reinventing indicator logic per
widget.

### 3. Panel container - `QDockWidget`-based, not the current unused `QGridLayout`

`ConstellationWindow.add_instrument(bridge, widget_cls)` wraps the resulting `InstrumentWidget` in
a `QDockWidget` and calls `self.addDockWidget(...)`. This gets you, for free, via Qt: drag-to-
rearrange, tabbing multiple instruments together, floating a panel out to its own window, and
saved/restored layouts (`QMainWindow.saveState()`/`restoreState()`) - all standard `QMainWindow`
dock behavior, not custom code.

### Registration, so "pass a driver, get a GUI" actually works

```python
@register_gui(Oscilloscope)          # keyed by category, not by specific driver model
class OscilloscopeWidget(InstrumentWidget):
    ...

# anywhere:
window.add_instrument(driver)        # looks up the category, builds the right Bridge + Widget
```

Because widgets are registered per *category* (matching how drivers are already written against
category APIs), any current or future driver in that category gets a working GUI automatically -
no widget code needed when someone adds a new oscilloscope model.

## What a new collaborator actually does

To add a GUI for a new category (say, a signal generator):
1. Copy `power_supply_gui.py` (proposed as the minimal reference example - fewer moving parts than
   the oscilloscope's plotting) as a template.
2. Assemble `TrackedControl`s for each category-level setting, using only `bridge`/category API -
   never a specific driver class.
3. `@register_gui(YourCategory)` the widget class.
4. That's it - `window.add_instrument(any_driver_in_that_category)` works, locally or networked,
   for every current and future driver in the category.

See `docs/gui_authoring_guide.md` for the step-by-step walkthrough of exactly this, plus the
`TrackedControl` states table above.

## Decisions (settled)

1. **Threads, not processes.** Per-instrument `threading.Thread`, owned entirely by
   `InstrumentBridge`. Hard requirement: **category widget authors never see the threading layer**.
   `InstrumentWidget.__init__` receives an already-constructed, already-running `bridge` - no
   widget author ever imports `threading`, touches a queue, or starts a bridge themselves. From
   their side it's plain single-threaded Qt: build layout, wire `TrackedControl`s to `bridge`,
   handle signals. `ConstellationWindow.add_instrument(...)` is the one and only place a bridge
   gets created and started.
2. **`QDockWidget`**, not `QMdiArea`.
3. **Setpoint-vs-actual stays GUI-layer-only** for now (lives entirely in `TrackedControl`, not in
   `InstrumentState`). Revisit as a `docs/dummy_and_state_review.md` follow-up if scripting ever
   wants the same tracking.
4. **Visual language and layout**, now concrete:
   - Front-panel mimicry means *logical grouping*, not a literal replica: e.g. for an oscilloscope,
     channel controls grouped together (one sub-group per channel), trigger controls in their own
     frame, run/stop/single-trigger acquisition controls set apart along the top or a side edge -
     matching how a real instrument's front panel separates these concerns, not any specific
     model's exact layout. Each category widget author makes this call for their instrument; the
     framework doesn't (can't) enforce a specific arrangement, only that groups are visually
     distinct (`QGroupBox`/`QFrame` per logical group is the expected pattern).
   - The indicator convention is a custom button class - `IndicatorButton` - used for every
     toggle/enum-style control. It shows the normal button content plus two small indicator lights
     (on the button face or immediately beside/above it): one reflecting the **setpoint** (what was
     last requested) and one reflecting **pending/mismatch/stale** status (see table below). Value
     entries (e.g. a volts/div field) use the same two-indicator convention via small colored
     markers next to the field rather than a button.
5. **Naming stays as proposed**: `InstrumentBridge`, `TrackedControl`, `register_gui`.
6. **`PyQt6`/`matplotlib` become hard dependencies** in `pyproject.toml` (not an optional extra).
