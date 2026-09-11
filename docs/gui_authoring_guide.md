# Writing a GUI for a new instrument category

This is the practical walkthrough. For the design rationale (why threads, why `QDockWidget`, why
setpoint-tracking lives in the GUI layer only), see `docs/gui_architecture_proposal.md`. This guide
assumes that's already decided and just tells you what to type.

Three finished examples to read alongside this guide, in order of complexity:
- `src/constellation/instrument_control/power_supply/power_supply_gui.py` - the minimal case.
  Copy this one first.
- `src/constellation/instrument_control/oscilloscope/oscilloscope_gui.py` - adds lazy per-channel
  construction and a manual (non-polled) data capture with a plot.
- `src/constellation/instrument_control/arb_waveform_generator/arb_waveform_generator_gui.py` -
  a plot *computed* from settings rather than captured (flagged by one subdued line, detail in its
  tooltip), a channel layout chosen from the View menu, and a control greyed out while the current
  waveform makes it meaningless.

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

### 3. Lay out controls with `Parameter*`, grouped like a front panel

Group related controls into `QGroupBox`/`QFrame` sections the way a real instrument's front panel
would (trigger controls together, per-channel controls together, run/stop controls set apart) -
see the state table below for what each control shows the user.

Three control types, all take a `bridge`, a `label`, a `get(state) -> value` callable, and a
`set_method` name (the driver method to call on write):

```python
ParameterToggle(bridge, "Output", get=lambda s: s.channels[1].enable,
                set_method="set_output_enable", set_args=lambda v: (1, v), get_args=(1,))

ParameterBox(bridge, "Voltage", get=lambda s: s.channels[1].voltage_set,
             set_method="set_voltage", set_args=lambda v: (1, v), get_args=(1,),
             validator=QDoubleValidator(), unit="V")

ParameterChoice(bridge, "Coupling", get=lambda s: s.channels[1].coupling,
                set_method="set_coupling", set_args=lambda v: (1, v), get_args=(1,),
                choices=[Oscilloscope.COUPLING_AC, Oscilloscope.COUPLING_DC])
```

Full details, including the density modes and what each lamp means, are in **The `Parameter*`
controls** below. The older `Tracked*` family is still present and still works, but is not what new
widgets should use.

`set_args` maps the raw UI value to the driver call's full argument list - default is `lambda v:
(v,)`, override it when the driver method needs extra positional args (a channel number, as above).

**Closures in a loop (per-channel controls): bind the loop variable as a default argument.**
Building N channels' worth of controls in a `for ch in range(...)` loop is the normal case - always
write `lambda s, ch=ch: s.channels[ch].voltage_set`, never `lambda s: s.channels[ch].voltage_set`
(the latter captures the loop variable itself, so every channel's control ends up reading the last
channel). Both `power_supply_gui.py` and `oscilloscope_gui.py` do this throughout.

### 4. Not everything is a parameter control

Two things that deliberately are *not* wrapped in a `Parameter*` control:

- **Actions without a setpoint** - Run/Stop/Single-trigger, "Capture Waveforms". These are plain
  `QPushButton`s wired straight to `bridge.request(...)` in a `clicked` handler (see
  `oscilloscope_gui.py`'s acquisition box). There's no "confirmed vs. requested" concept for an
  action - which is also why the hardware suite can only ever mark them `confirmed`, never
  `roundtrip`.
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

### Keep the panel about the controls

The instrument's controls should be the most prominent thing on a panel. Warnings and caveats get
one short line in a subdued style, with any detail in a tooltip - not a multi-line block in a
coloured box. Display preferences go in the window's **View** menu, not on the panel: override
`has_view_actions()` (return True) and `add_view_actions(menu)` on your widget, and
`ConstellationWindow` builds the menu - flat for one instrument, a submenu per panel for several.
The menu is rebuilt whenever an instrument is added, so create fresh actions on each call and tick
them from current state.

### Switching a layout: reparent, never rebuild

If a panel offers more than one arrangement of the same controls (the AWG's side-by-side vs tabs
choice), build the controls **once** and move them between containers. Rebuilding silently throws
away each control's setpoint and any request still pending on it. `QSplitter.addWidget` and
`QTabWidget.addTab` both reparent; empty a `QTabWidget` with `removeTab()` (which detaches without
deleting) before moving its pages elsewhere, rather than relying on it noticing they left.

### Greying out a control that doesn't currently apply

`setEnabled(False)` on the whole control is safe to combine with the verification machinery, which
disables a control's *inner* widgets for a method the hardware can't do. Record `isEnabled()` at
build time and only ever narrow it (`setEnabled(built_enabled and applies)`), so a state update
never re-enables a control for an unavailable feature.

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

## The older `Tracked*` status convention

`Tracked*` predates `Parameter*` and is documented here because both category GUIs used it until
recently and it is still in `ui.py`. New widgets should use `Parameter*`.

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

Note the `mismatch` comparison here is exact equality, which means an instrument that quantizes
(ask a scope for 0.55 V/div, get 0.5) sits on red forever. The `Parameter*` controls below fix that
with a tolerance, and separate this one lamp into the three independent questions it is currently
collapsing.

## The `Parameter*` controls

`ParameterBox` / `ParameterToggle` / `ParameterChoice` are what a category widget should place for
every instrument *setting*. Each one carries its own label, its own lamps and its own detail
window, so the panel code places one widget per setting rather than a label plus a control plus
hand-placed indicators.

```python
ParameterBox(bridge, "V/div", get=lambda s: s.channels[1].div_volt,
             set_method="set_div_volt", set_args=lambda v: (1, v),
             get_args=(1,), unit="V", tolerance=0.01)
```

Same `bridge`/`get`/`set_method`/`set_args` interface as the older `Tracked*` controls, plus:

| argument | meaning |
| --- | --- |
| `get_method` | the getter to call for a re-read. Derived from `set_method` by the `set_x`/`get_x` convention; pass it for anything that doesn't follow it. |
| `get_args` | the **getter's** arguments (a channel number), used by the PV button. Not the same as `set_args`. |
| `tolerance` / `abs_tolerance` | how close a read-back has to be to count as agreeing. |
| `view` | initial density — see below. |
| `edit_width` | width of the editor, in px. |
| `prefixes` / `auto_prefix` | unit-prefix selector — see below. |
| `lcd` | show the measured value on a `QLCDNumber` (`ParameterBox` only). |

A control never writes to the instrument unless the user actually typed in it: `editingFinished`
fires on Return and on focus-out whether or not anything changed, so committing an untouched field
sends nothing. Numeric fields also ignore the system locale — they are displayed with `str()` and
parsed with `float()`, so the validator is pinned to `.` decimals. Left on a locale where `.` is
the thousands separator (German, Dutch regions), the validator stripped it on every commit and
`2.0` grew to `20`, `200`, ...

**The setpoint follows the instrument.** When a read-back differs from the setpoint (beyond the
control's tolerance), the setpoint takes the instrument's value - so a change made on the front
panel, or by another program, appears in the SP field and not only on the PV row. Two things hold it
off: the user is part-way through typing, or one of the control's own set requests has not been
answered yet. The second matters because the bridge may emit a state it read *before* the command
ran; the bridge answers each command before the state read that follows it, so once every request
is answered (matched on its arguments, so channel 2's answer does not release channel 1) the next
value is fresh. A failed send also counts as answered, which lets the instrument's real value back
in. The detail window's **Update GUI controls from instrument** checkbox turns this off per control
(`control.set_follow_instrument(False)`). With it on, a value the instrument quantizes or refuses
snaps the setpoint to what was accepted, so the grey *differs* lamp is mostly seen with it off.

A control never overwrites input the user has typed but not committed. (The guard for this used to
be `hasFocus()`, which is False whenever the window is not the *active* window — so a background
poll silently replaced half-typed text with the last known value, and a field sitting at `0` ate
`0.002` and looked like it had rounded. It tracks uncommitted edits via `textEdited` now.)

### Density: `ParameterView`

The same control renders at two densities, set per control and changeable live:

```
FULL                              COMPACT
  Parameter Name
◀SP [ 0.55  ] V    ● ● ●          Name: [ 0.55 ] V   ● ●
PV▶ [ 0.5   ] V
```

- **`ParameterView.FULL`** — title, setpoint row, measured row, three lamps.
- **`ParameterView.COMPACT`** — the default, and what the category GUIs use. Inline label,
  setpoint row, two lamps.

COMPACT drops the **verification** lamp, not one of the runtime lamps. Verification is a fixed
property of the driver and its records — it cannot change while a panel is open, so it is reference
material rather than something to monitor. Which of "did my command land" and "does the instrument
agree" is failing is the live question, and both have different fixes, so a compact panel keeps
both. Verification stays one lamp-click away in the detail window, and a method the hardware cannot
do still comes up as a disabled control either way.

Because the label is inside the control in COMPACT, a panel places one widget per row rather than a
`QLabel` and a control:

```python
for row, control in enumerate((enable, vdiv, voff, coupling)):
    layout.addWidget(control, row, 0)
layout.setColumnStretch(1, 1)     # slack goes into the empty column, not into the controls
```

`control.set_view(ParameterView.FULL)` switches density in place, keeping setpoint and read-back.

### Switching many controls at once

- **`--full` / `--compact`** — `add_view_arguments(parser)` adds both (mutually exclusive) to an
  `argparse` parser; pass `view_from_arguments(args)` as `ConstellationWindow(parameter_view=...)`.
  Every GUI demo in `examples/` takes them.
- **View menu** — *All Controls: Full / Compact* for the whole window (also the default for anything
  added afterwards), and with several instruments docked, *Controls: Full / Compact* in each
  panel's submenu. In code: `window.set_parameter_view(view)` / `widget.set_parameter_view(view)`.
- **Apply to similar** — a button in the detail window. Copies the control's density, LCD and
  follow-the-instrument options to every *similar* control: the same kind of control driving the
  same method, on the same panel — i.e. the same parameter on every other channel. Other
  instruments' panels are never touched.

A default view reaches controls built lazily too: `InstrumentWidget` connects a slot to
`state_changed` *after* `on_state_changed`, so it sees per-channel controls built from the first
update. It applies once per control, so a control switched by hand is not switched back by the next
poll. A category widget needs no code for any of this.

### Unit prefixes

Pass `prefixes=True` for the full SI set, or a tuple of symbols to narrow it. The selector sits
immediately right of the setpoint field and doubles as its unit label; the measured row gets a
matching label, so **both rows always read in the same units** — which is the entire point of having
two rows to compare.

```python
ParameterBox(bridge, "Offset", get=lambda s: s.offset_time,
             set_method="set_offset_time", unit="s", prefixes=("", "m", "µ", "n"))
```

The user types `2`, picks `ms`, and the driver is asked for `0.002`. Scaling is display-only:
changing the prefix sends nothing, because the user asked to see the same quantity in different
units, not to change it.

`auto_prefix=True` (the default) picks a readable prefix from the **first** non-zero value and then
stops — a control whose units keep moving while you read it is worse than one that shows `0.002`,
and it never overrides a prefix the user chose. Zero is ignored, since zero is zero in every prefix
and would otherwise pin the selector at femto.

Reach for this on anything whose natural values are far from 1: timebases, currents, frequencies.

### LCD readout

`ParameterBox` shows the measured value on a `QLCDNumber` instead of a text field in the full view
by default (`lcd=False` for a text field). There is also a checkbox in the detail window, so it can be turned on for one
parameter while watching it. Only numeric parameters offer it — there is nothing for seven segments
to show for a coupling mode, and `supports_lcd` is False on the toggle and choice controls.

### Clicking things

- **Any lamp** opens that parameter's detail window. Its lamp table answers the two questions a
  user has about the dots: *which lamp is which* (rows top to bottom, in the order they sit on the
  control; a lamp the current view hides is marked "not shown in this view") and *what each colour
  means* (a legend of every colour with a meaning of five words or fewer, the current one in bold).
  Full explanations are tooltips - the `*_TEXT` tables - and the short meanings are `*_SHORT`;
  both hang off `LAMP_KINDS`, so the control and the window cannot drift apart. Below the table:
  the density dropdown, the last value sent and received, any send/read error, the driver call,
  and the SCPI behind it when there is any. It follows live, so it can be left open while the instrument is
  poked. There is no SCPI to show for a dummy driver (nothing touches a relay) or an
  `ObserverBridge` (the call happens in another process) — the window says so rather than showing a
  blank field.
- **SP** re-sends the current setpoint; **PV** re-queries the instrument. SP does nothing when no
  setpoint has been set — a refresh-looking click must never invent a value and write it to
  hardware.

### `ParameterToggle`

A checked `QPushButton` is nearly indistinguishable from an unchecked one under several dark
themes, which is unfortunate for an output-enable control. `ParameterToggle` puts a large lamp to
the left of the button, drawn from `assets/indicator_1.png` / `indicator_0.png` and overridable per
control:

```python
ParameterToggle(bridge, "Channel 1", ..., on_text="LIVE", off_text="SAFE",
                on_pixmap=my_pixmap, off_pixmap=my_other, indicator_size=28)
```

The lamp beside the button follows the **button**, instantly — the two can never disagree. What
the instrument reports is the PV row's lamp (full view) and the value status lamp (both views), so
a button that was clicked and did nothing still shows up, as a mismatch there. If the artwork can't be
loaded it falls back to a painted circle rather than becoming an invisible control.

The two views label things differently, because in each one the button is the only element free to
say something:

- **FULL** — the title carries the parameter name, so the button carries the **state**
  (`on_text`/`off_text`, defaulting to "Enabled"/"Disabled"), and the measured row is a second
  indicator lamp showing what the instrument reports. Both rows are icon-then-lamp with identically
  sized lamps, so the two line up in a column and read as "asked" above "actually".
- **COMPACT** — there is no title, so the button carries the **name** and the state lives entirely
  in the lamp beside it. No inline label, since that would say the name twice.

### Sizing

A `Parameter*` control does not absorb slack in either direction: horizontally `QSizePolicy.Maximum`
(may shrink in a cramped panel, never grows), vertically `Fixed` (never grows *or* shrinks — plain
`Maximum` also permits shrinking, which squashed a control switched to the taller full view into the
row height the compact one had needed). Its editor and PV widget have fixed widths, and the internal
row spacing is constant.

So extra space in a container lands **between** whole controls rather than being spread through one
control's internals. Give containers an empty stretch column and row:

```python
layout.setColumnStretch(1, 1)
layout.setRowStretch(len(controls), 1)
```

The lamp column centres on the **field rows only**, not the title — the lamps report on those rows,
so centring over the title too pushed them out of line with what they describe.

### `Tracked*` vs `Parameter*`

`Tracked*` is the older, simpler family: one box, two lamps, no read-back row, no detail window, and
exact-equality comparison. It still works and is used nowhere structural. Prefer `Parameter*` for
new work.

`examples/parameter_widgets_demo.py` runs the whole family at all three densities against two dummy
scopes, with buttons that simulate a failed send and a dropped connection.

## Window chrome: shortcuts, folding, resizing

### Shortcuts

`ConstellationWindow` installs **Cmd/Ctrl-W** (close this window) and **Cmd/Ctrl-Q** (quit) for
you. Any other top-level window Constellation puts on screen should call the same helper:

```python
from constellation.ui import install_window_shortcuts

install_window_shortcuts(my_dialog, on_close=my_dialog.reject)
```

They are installed directly rather than relying on the menu bar, because the menu is optional
(`add_menu=False`) and on some platforms a menu action's shortcut is not live until the menu has
been shown. `QKeySequence.keyBindings()` supplies the platform-correct sequence, with an explicit
`Ctrl+W`/`Ctrl+Q` appended where the platform provides none (Windows has no standard Quit
shortcut). Shortcuts are `WindowShortcut`-scoped, so a dialog's Cmd-W closes the dialog rather than
the panel behind it.

### `CollapsiblePanel`

Use this instead of `QGroupBox` for a section of a front panel:

```python
panel = CollapsiblePanel("Trigger")
panel.set_content_layout(my_layout)
```

Clicking the header folds the section away. Not `QGroupBox.setCheckable(True)`, for two reasons: a
checkable group box *disables* its contents rather than hiding them, so it frees no space at all;
and its checkbox reads as "this feature is off", which is a completely different claim from "I have
folded this away".

`fold=` chooses which dimension collapsing gives back — `Qt.Orientation.Vertical` (the default)
surrenders height, `Horizontal` surrenders width. Use `Horizontal` for panels sitting in a row of
columns, like a per-channel strip: folding one should give its width to its neighbours rather than
leaving an empty column.

Collapsing clamps the widget's maximum size. Hiding the contents alone is not enough — a splitter
keeps holding the old size, and the panel becomes a blank gap instead of releasing its room.

### `make_splitter`

Nest splitters instead of fixing a grid, so the person using the instrument can give the plot more
room or shrink the channel strip:

```python
side  = make_splitter(Qt.Orientation.Vertical, trigger_box, horiz_box)
upper = make_splitter(Qt.Orientation.Horizontal, plot, side, stretch=[3, 1])
body  = make_splitter(Qt.Orientation.Vertical, upper, channels_box, stretch=[4, 1])
```

**Use `stretch=`, not `sizes=`.** `QSplitter.setSizes([3, 1])` means three *pixels* and one pixel,
which Qt then clamps up to the children's minimums — a ratio passed there silently does nothing.
Stretch factors are proportions, and they survive a resize.

The helper also widens the handle (Qt's 1px default hairline is undiscoverable, so panels look
fixed even when they aren't) and turns off `childrenCollapsible`: the panel header is the honest way
to fold something away, and a child dragged to zero width just looks broken.

`oscilloscope_gui.py` is the worked example of all three.

## The status bar and sync settings

Every `ConstellationWindow` has a status bar, with or without a menu bar. It holds a **Config**
button that opens the sync settings for each docked instrument, applied immediately:

- **Poll instrument, every N s** - `bridge.set_polling(enabled, interval_s)`. On by default (2 s).
  With it off, the instrument is read only when a command runs, and a command no longer triggers a
  full refresh of every parameter - only the value it set is read back. An `ObserverBridge` has
  `supports_polling = False` (its updates arrive when the owning process broadcasts), and the
  window greys the row out.
- **Auto-send panel values, every N s** - `widget.set_auto_send(enabled, interval_s)`. Off by
  default. Re-sends every value the *user* has set on the panel (`control.user_setpoint`), so the
  instrument is held at what the panel says; controls that have only mirrored the instrument are
  never sent.

Settings persist between runs, via `QSettings`, keyed by driver class and address
(`bridge.settings_key()`), so the same instrument at the same address gets them back and other
instruments are unaffected. They are restored before the bridge starts, so a saved "don't poll" is
honoured from the first moment. Setting `CONSTELLATION_SETTINGS_FILE` to a path keeps them in that
INI file instead - the test suite does this for every test, so a test run never touches a real
user's settings.

Polling on, auto-send off is **monitoring**: the panel follows the instrument and writes to it only
when the user changes something. Nothing in a category widget needs to do anything for this to
work - `push_setpoints()` finds every `Parameter*` control on the panel itself.

## The Instrument and View menus

`ConstellationWindow` builds an **Instrument** menu from the panels docked into it: Refresh State,
Apply State, Save State…, Load State…, and Get Connection Info…. With one instrument the actions sit
directly in the menu; with several, each gets a submenu named after its panel — "Refresh state" is
ambiguous the moment a second instrument is on screen.

Everything goes through `bridge.request()`, never a Driver call from the GUI thread: a state refresh
takes seconds on real hardware and would freeze every other panel in the window.

Note that **Load State does not touch the instrument** — `Driver.restore_state()` only refills the
driver's own state object. The menu says so in a dialog after loading, because otherwise a user
watches the hardware not move and concludes the feature is broken. Apply State is the second half.

`ConnectionInfoDialog` shows where an instrument is and whether it can currently be reached. Static
addressing comes from `bridge.describe()` — implemented on the bridge because an `ObserverBridge`
has no Driver at all, and reading one from the GUI thread races the worker. Live status comes from a
`connection_summary` request. Only a networked relay is labelled with broker/labmesh fields; a local
`DirectSCPIRelay` also carries an `address`, and calling that a "labmesh relay id" would be a
confident lie about a USB cable.

## Saving traces

`SaveTraceDialog` writes whatever is on a plot in one of five formats. A category widget converts
what it holds into generic `Trace` records — a labelled pair of x/y arrays, which is as true of a
VNA sweep as a scope capture — and hands them over:

```python
SaveTraceDialog(self._traces(), figure=self.plot_widget.fig1,
                metadata={"instrument": self.panel_title}, parent=self, log=self.log).exec()
```

| format | what it is for |
| --- | --- |
| TOME | archive container, for putting a capture into a nebula session with its metadata |
| GrAF | the whole figure — data, axes, styling — reopenable and re-styleable |
| JSON | self-describing text; easiest to read back from another language |
| CSV | plain columns for a spreadsheet |
| PNG | a picture of the plot; the numbers are not recoverable from it |

Two things worth knowing:

- **CSV goes wide only when the traces share an x axis** (a scope's channels share a timebase, so
  one x column and one column per channel is what a person expects). Mismatched x axes are written
  long-form, because a wide table would silently imply a row-by-row correspondence that does not
  exist.
- **A format whose writer is missing is shown disabled with the reason, not hidden.** A silently
  absent option looks like the feature does not exist. This is currently how TOME appears: no TOME
  writer is published anywhere in the toolchain yet. `_tome_writer()` in `ui.py` is the single place
  to wire one up — it probes a few plausible names and reports unavailable until one exists.

Add a format by extending `TRACE_EXPORTERS` and `TRACE_EXPORT_NOTES`; the dialog builds itself from
those, including the availability check and whether the format needs a figure.

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
- [ ] Actions (no setpoint) are plain buttons; settings (have a setpoint) are `Parameter*`;
      read-only measurements are plain labels updated from `on_state_changed`
- [ ] Containers give `Parameter*` controls an empty stretch column so slack lands between them
- [ ] Numeric controls that an instrument will quantize carry a sensible `tolerance`/`abs_tolerance`
- [ ] Parameters whose natural values are far from 1 (timebases, currents) carry `prefixes=`
- [ ] Anything slow is behind an explicit button, not folded into automatic polling
- [ ] Channel/index counts come from `state`, not from a driver attribute
- [ ] Smoke-tested headlessly against a `dummy=True` driver
