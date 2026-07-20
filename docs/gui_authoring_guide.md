# Writing a GUI for a new instrument category

This is the practical walkthrough. For the design rationale (why threads, why `QDockWidget`, why
setpoint-tracking lives in the GUI layer only), see `docs/gui_architecture_proposal.md`. This guide
assumes that's already decided and just tells you what to type.

Two finished examples to read alongside this guide, in order of complexity:
- `src/constellation/instrument_control/power_supply/power_supply_gui.py` - the minimal case.
  Copy this one first.
- `src/constellation/instrument_control/oscilloscope/oscilloscope_gui.py` - adds lazy per-channel
  construction and a manual (non-polled) data capture with a plot.

## The five-minute mental model

- `ConstellationWindow.add_instrument(driver=...)` builds a background `InstrumentBridge` for your
  driver, looks up your registered widget class by category, constructs it, and docks it. **You
  never construct a bridge and never touch threading** - by the time your widget's `__init__` runs,
  `bridge` is already alive and already running.
- Your widget only ever talks to the bridge two ways:
  - **Write**: `self.bridge.request("set_voltage", channel, 3.3)` - fire-and-forget, the outcome
    comes back later via a signal. Same call whether the instrument is local or on the other side
    of labmesh.
  - **Read**: connect to `bridge.state_changed` (or just use `TrackedControl`s, which already do
    this for you) - never read `bridge.driver` directly.
- **Never touch `bridge.driver`.** This is the one hard rule. It's what keeps a slow/stuck
  instrument from freezing every other panel, and what avoids racing the bridge's own worker thread
  over the driver's state. If you find yourself wanting `bridge.driver.something`, you want
  `bridge.request("something", ...)` or a field out of the `state` object from `state_changed`
  instead.

## Step by step

### 1. Copy the template

Start from `power_supply_gui.py`, not the oscilloscope one - fewer moving parts. Rename the file
and the class.

### 2. Register it

```python
@register_gui(YourCategory)          # the category class, e.g. PowerSupply, Oscilloscope
class YourCategoryWidget(InstrumentWidget):
    def __init__(self, main_window, bridge, log):
        super().__init__(main_window, bridge, log)
        ...
```

Registration is keyed by **category**, not by driver model - once `YourCategoryWidget` exists,
every current and future driver in `YourCategory` gets a working GUI automatically via
`window.add_instrument(any_driver_of_that_category)`.

### 3. Lay out controls with `Tracked*`, grouped like a front panel

Group related controls into `QGroupBox`/`QFrame` sections the way a real instrument's front panel
would (trigger controls together, per-channel controls together, run/stop controls set apart) -
see the state table below for what each control shows the user.

Three control types, all take a `bridge`, a `label`, a `get(state) -> value` callable, and a
`set_method` name (the driver method to call on write):

```python
TrackedToggle(bridge, "Output", get=lambda s: s.channels[1].enable,
              set_method="set_output_enable", set_args=lambda v: (1, v))

TrackedValue(bridge, "Voltage", get=lambda s: s.channels[1].voltage_set,
             set_method="set_voltage", set_args=lambda v: (1, v),
             validator=QDoubleValidator(), unit="V")

TrackedChoice(bridge, "Coupling", get=lambda s: s.channels[1].coupling,
              set_method="set_coupling", set_args=lambda v: (1, v),
              choices=[Oscilloscope.COUPLING_AC, Oscilloscope.COUPLING_DC])
```

`set_args` maps the raw UI value to the driver call's full argument list - default is `lambda v:
(v,)`, override it when the driver method needs extra positional args (a channel number, as above).

**Closures in a loop (per-channel controls): bind the loop variable as a default argument.**
Building N channels' worth of controls in a `for ch in range(...)` loop is the normal case - always
write `lambda s, ch=ch: s.channels[ch].voltage_set`, never `lambda s: s.channels[ch].voltage_set`
(the latter captures the loop variable itself, so every channel's control ends up reading the last
channel). Both `power_supply_gui.py` and `oscilloscope_gui.py` do this throughout.

### 4. Not everything is a `TrackedControl`

Two things that deliberately are *not* wrapped in `Tracked*`:

- **Actions without a setpoint** - Run/Stop/Single-trigger, "Capture Waveforms". These are plain
  `QPushButton`s wired straight to `bridge.request(...)` in a `clicked` handler (see
  `oscilloscope_gui.py`'s acquisition box). There's no "confirmed vs. requested" concept for an
  action.
- **Read-only measured values** - a power supply's measured voltage/current, for example. There's
  no setpoint for a measurement, only a reported number, so the pending/mismatch/stale machinery
  doesn't apply. Just a plain `QLabel` updated from `on_state_changed()` (see
  `power_supply_gui.py`'s `_update_measurements`).

### 5. Handle anything beyond per-field controls in `on_state_changed`

Override `on_state_changed(self, state)` for anything that isn't a single field - building
per-channel UI lazily once you know how many channels exist, redrawing a plot, updating a read-only
label. `state` is a freshly reconstructed `InstrumentState` (never a live `driver.state`
reference), safe to read from the GUI thread.

Use `state.first_channel`/`state.num_channels` to discover channel count, **not** a driver
attribute like `driver.max_channels` - a widget backed by an `ObserverBridge` (watching another
process's instrument over labmesh) has no local `Driver` to read attributes off of at all. Reading
only from `state` is what makes your widget work identically whether it's driving the instrument
directly or just observing it.

```python
def on_state_changed(self, state):
    if not self._channels_built:
        self._build_channels(state)   # first update only
```

### 6. Slow operations stay manual, never folded into polling

`OwningBridge` polls `driver.poll()` automatically every couple seconds for cheap state (settings,
quick measurements). Anything that can legitimately take seconds - a full oscilloscope waveform
capture is the canonical example - must **not** happen as part of that automatic poll. Give it its
own button that calls `bridge.request(...)` explicitly, and pick the result up via
`bridge.command_result` if you need to know when it lands (see
`oscilloscope_gui.py::_on_command_result`).

Conversely, if your category's "expensive" read is actually cheap (e.g. `PowerSupply`'s
`get_measured_output()` is a single quick query, so it's already folded into `refresh_state()` and
arrives for free with every `state_changed`), don't add a needless capture button - just read it in
`on_state_changed`, as `power_supply_gui.py` does.

## The status indicator convention

Every `Tracked*` control shows two small lights (or, for `TrackedToggle`'s `IndicatorButton`, one
light plus the button's own on/off face): a **setpoint** light (green=last-requested-ON,
dark=last-requested-OFF, grey=nothing requested yet) and a **status** light:

| Status | Meaning | Color |
|---|---|---|
| `confirmed` | last `state_changed` matches what was last requested | green |
| `pending` | user changed it, waiting on `command_result` | amber |
| `mismatch` | confirmed value differs from what was requested (write failed, or something else changed it) | red |
| `stale` | no `state_changed` received recently (bridge offline, or instrument unresponsive) | grey |

This is implemented once, in `_TrackedControlBase._status()` (`src/constellation/ui.py`) - you
don't reimplement it per category, only per control instance via the `get`/`set_method`/`set_args`
you pass in.

## Testing your widget headlessly

No display needed - Qt has an offscreen platform plugin, and a `dummy=True` driver gives you
something to point the widget at without hardware:

```bash
QT_QPA_PLATFORM=offscreen python -c "
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer
import pylogfile.base as plf
from constellation.instrument_control.power_supply.drivers.Rigol_DP832_dvr import RigolDP832
from constellation.ui import ConstellationWindow
import constellation.instrument_control.power_supply.power_supply_gui  # registers the widget

app = QApplication([])
log = plf.LogPile()
driver = RigolDP832('DUMMY', log, dummy=True)
win = ConstellationWindow(log, add_menu=False)
win.add_instrument(driver=driver)

QTimer.singleShot(4000, app.quit)
app.exec()
"
```

Drive it further by reaching into `win.instrument_widgets[0].channel_controls[...]` and calling a
control's `_user_changed(value)` to simulate user input, then checking `._status()` after the next
poll cycle - this is how both reference widgets were verified end-to-end (pending → confirmed
transition, correct channel count from `state.num_channels`, live measurement labels) without any
real hardware or a visible display.

## Checklist before you're done

- [ ] `@register_gui(YourCategory)` on the widget class
- [ ] No code path touches `bridge.driver` or any `Driver` method/attribute directly
- [ ] Per-channel/per-index closures bind the loop variable as a default argument
- [ ] Actions (no setpoint) are plain buttons; settings (have a setpoint) are `Tracked*`; read-only
      measurements are plain labels updated from `on_state_changed`
- [ ] Anything slow is behind an explicit button, not folded into automatic polling
- [ ] Channel/index counts come from `state`, not from a driver attribute
- [ ] Smoke-tested headlessly against a `dummy=True` driver
