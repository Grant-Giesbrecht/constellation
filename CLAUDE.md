# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Constellation (PyPI: `constellation-py`, package name `constellation-core`) is a Python library for
instrument control in scientific/lab settings, built on top of `pyvisa`/`pyvisa-py`. It provides a
standardized driver API across instrument categories (oscilloscopes, VNAs, power supplies, spectrum
analyzers, DMMs, arb waveform generators), rich logging of every instrument command via `pylogfile`,
and optional AES-encrypted networking so instruments can be controlled/monitored remotely.

## Commands

- Install for development: `pip install -e .`
- Run tests: `pytest tests/` (uses plain `pytest`, no config file/markers — `assert`-based functions in
  `tests/test_base.py` and `tests/test_dummy_state.py`). Run a single test:
  `pytest tests/test_base.py::test_interpret_range`.
  Some tests use `xfail(strict=True)` to pin down a *confirmed* bug by asserting the desired behavior — a
  reported XPASS means the bug got fixed and the marker should be removed, not that the test is broken.
- There is no lint/format tooling configured in this repo — don't invent one.
- Docs are built with Sphinx from `docs/` (see `docs/conf.py`, `.readthedocs.yaml`); not part of normal dev loop.
- Indentation in this codebase is tabs, not spaces — match the surrounding file.

## Architecture

### Category → Driver → (Mixin) hierarchy

Each instrument type (oscilloscope, vector_network_analyzer, power_supply, digital_multimeter,
spectrum_analyzer, arb_waveform_generator) lives under `src/constellation/instrument_control/<category>/`
and follows the same three-layer pattern:

1. **Category class** (`<category>_ctg.py`, e.g. `oscilloscope_ctg.py`): defines an abstract `Driver`
   subclass (e.g. `Oscilloscope`) that is the common API every driver of that category must implement,
   plus the `InstrumentState` subclasses describing that category's state shape (e.g. `OscilloscopeState`,
   `OscilloscopeChannelState`). Category classes define abstract `set_*`/`get_*` methods; concrete behavior
   lives in drivers.
2. **Driver class** (`<category>/drivers/<Vendor>_<Model>_dvr.py`, e.g. `Rigol_DS1000Z_dvr.py`): implements
   the category's abstract methods with vendor-specific SCPI commands. Drivers use the `@superreturn`
   decorator on `set_*`/`get_*` methods — it calls the driver's own method body first (skipped entirely in
   dummy mode), then automatically forwards to the parent category class's same-named method, so state
   tracking in the base class runs uniformly. A driver getter communicates its parsed value by simply
   **returning** it; `@superreturn` captures that into `self._super_hint`, which the category method reads.
   Drivers must never assign `self._super_hint` themselves (a test enforces this). `@superreturn` is a
   descriptor class, not a function decorator, so it can capture its defining class via `__set_name__` —
   using `super(type(self), self)` would recurse on any subclassed driver. See `docs/superreturn.md`.
3. **Mixins** (e.g. `MeasurementsMixin` in `oscilloscope_ctg.py`): optional capabilities not all drivers of
   a category support. A mixin declares `__state_key__` (name in `state.state_fragments`) and
   `__state_fragment__` (its `InstrumentState` subclass); `Driver.discover_mixins()` walks the MRO at
   `__init__` time and auto-registers any mixins the concrete driver class inherits from.

New drivers should only need to translate SCPI commands in the driver file — the category class and
`Driver` base handle state tracking, logging, online-status checks, and dummy-mode plumbing.

Not every instrument can do everything its category declares — a Rigol DS1000E has no SCPI
timebase control at all. Such a driver still defines **every** abstract method, marking the
impossible ones `@feature_unavailable("<what the hardware can't do>")` (never combined with
`@superreturn`, since no value is produced to track). The class stays constructible, a direct call
raises `FeatureUnavailable`, `Driver.unavailable_features()`/`feature_is_available()` report the
gaps before they're called, and `refresh_state`/`apply_state`/`refresh_data`/`init_dummy_state`
skip them instead of aborting the sweep. `RigolDS1000E` is the reference example; see
`docs/partial_compliance.md`.

### State tracking (`src/constellation/base.py`)

- `InstrumentState` (Serializable, from `stardust`) holds all tracked parameters for a driver/category.
  Subclasses call `self.add_param(name, unit=..., value=...)` in `__init__` and list every param name in
  `__state_fields__`; `validate()` cross-checks the two stay in sync and warns (via `self.log`) if not.
  Don't call `validate()` by hand — `InstrumentState.__init_subclass__` wraps every subclass's
  `__init__` so it runs automatically after construction, which is the only point that reaches
  nested and lazily-built state objects too.
- `IndexedList` represents per-channel/per-trace state (e.g. one `OscilloscopeChannelState` per channel),
  1-indexed or otherwise offset via `first_index`. Iterating (`for x in indexed_list`) only yields
  populated slots; use `populated_items()` for `(index, value)` pairs when the index is also needed.
- `Driver.modify_state(query_func, params, value, indices=None, fragment=None)` is the single choke point
  all `get_*`/`set_*` calls go through to update `self.state`: if `query_func` is None, or the driver is in
  `dummy` mode, or `blind_state_update` is set, the passed `value` is written straight into state; otherwise
  `query_func()` is called to read the authoritative value back from hardware.
- `state.set(params, value, indices=...)` / `state.get(params, indices=...)` navigate nested state by
  walking a tuple of attribute names, descending into `IndexedList`s using the parallel `indices` tuple.

### Connection layer: Driver → CommandRelay

`Driver` never talks to `pyvisa` directly — it delegates all `write`/`read`/`query`/`connect`/`close` calls
to a `CommandRelay` (`src/constellation/relay.py`), which is swappable per-driver at construction time:
- `DirectSCPIRelay` — local `pyvisa` connection (the default for most drivers).
- `VICPDirectSCPIRelay` — VICP protocol via `pyvicp` (needed for LeCroy scopes, which don't speak plain VISA).
- `RemoteTextCommandRelayClient`/`...Listener` — routes commands over Constellation's network layer instead
  of talking to hardware locally.

This indirection is what lets the exact same driver class run against real hardware, a remote instrument
over the network, or (via `dummy=True` on `Driver`) a simulated instrument with no relay activity at all,
so code can be developed and tested without physical instruments attached.

Dummy mode has **one** dispatch point: `Driver.modify_state()`. If a `set_*`/`get_*` maps to a field in
`self.state` it needs no dummy-specific code at all — in dummy mode setters store the passed value and
getters read the tracked value back. `@enabledummy` + `dummy_responder()` is a narrow escape hatch reserved
for values dummy mode must *invent* (`get_waveform` synthesizing a sine, `get_measured_output` adding noise
to a setpoint) and for pure actions with no state. Putting `@enabledummy` on a plain setter is a bug: it
bypasses `modify_state()`, so the value is silently dropped. A test pins down the exact set of methods
allowed to carry it. See `docs/dummy_mode.md`.

### Networking (labmesh)

Built on the external `labmesh` package (ZeroMQ mesh: `DirectoryBroker`, `RelayAgent`,
`DirectorClientAgent`, `DataBank`). The older `pyfrost`-based stack (`network.py`, `net_client.py`,
`net_server.py`, `NetworkCommand`/`GenCommand`/`Packable`) has been fully removed — if you see those
names anywhere, they're stale references, not code.

The design is **"smart client, dumb relay"** (see `docs/labmesh_migration_plan.md`): the machine physically
wired to the instrument runs a driver-agnostic SCPI text relay, and the `Driver` — with all its state
tracking — lives in whichever process actually controls the instrument. The two halves live in
`relay.py`, not in `networking/`:

- `RemoteTextCommandRelayListener` — wraps a local `DirectSCPIRelay`/`VICPDirectSCPIRelay` and is handed to
  a `labmesh.RelayAgent` on the bench machine. It knows nothing about categories, drivers or state.
- `RemoteTextCommandRelayClient` — a `CommandRelay` that tunnels `write`/`read`/`query`/`query_binary` to
  that listener. Binary blocks cross as base64-encoded little-endian packed values (JSON has no bytes type),
  and get a longer timeout than text calls since a full-memory waveform read legitimately takes 15-20+ s.

Swapping `DirectSCPIRelay()` for `RemoteTextCommandRelayClient(...)` (and passing a labmesh `relay_id` as
`address` instead of a VISA resource string) is the *only* difference between local and networked use —
every driver takes `relay=` for exactly this reason.

Relays swallow their own exceptions and return a bare success flag, so each one records *why* it
failed on itself (`relay.last_error_kind`, a `RelayErrorKind`) for the Driver to act on:
`TRANSPORT` is retried and marks the driver offline, `INSTRUMENT` (a reply that wouldn't parse) is
retried but leaves it online, `USAGE` (unsupported operation, bad arguments) is neither retried nor
treated as a connection failure. New relay `except` blocks must call `self.note_failure(e)`.

How a driver reacts to a failed relay call is a per-driver `ReconnectPolicy`, passed as
`reconnect_policy=`: **in-call retry** (default ON) absorbs the sub-second blip so the driver
never goes offline, and **reconnect-on-use** (default OFF) lets an offline driver attempt a full
`connect()` on next use, at most once per cooldown. Without a policy a driver gets a *copy* of
`DEFAULT_RECONNECT_POLICY` — a copy, so tuning one instrument doesn't retune the rest. Note that
with reconnect-on-use off, an offline driver has no automatic path back: `write`/`read`/`query`
early-return on `not self.online`, and `check_online()` is only reachable from inside those
methods' `except` blocks.

`src/constellation/networking/labmesh_net.py` covers the other half: `DriverStateBroadcaster` runs a
`labmesh.RelayAgent` around an already-connected `Driver` in a background thread, so *other* (non-owning)
clients can subscribe to its state without controlling the instrument.

### Logging

All driver activity logs through `pylogfile` (`plf.LogPile`), passed into every `Driver` at construction.
Use the `Driver.debug/info/warning/error/critical` wrapper methods (not `self.log` directly) inside driver
code — they prefix messages with the instrument's `Identifier.short_str()` for traceability across multiple
connected instruments.

### Directories to know about

- `src/constellation/instrument_control/to_reformat/` and `to_extended/` — legacy/unmigrated
  drivers and category classes not yet converted to the current `Driver`/`CommandRelay`/`InstrumentState`
  pattern described above. Don't use these as a reference for new code; treat them as in-progress.
- `examples/` — runnable scripts demonstrating dummy-mode and hardware usage per category
  (`*_dummy_demo.py`, `*_hardware_demo.py`), plus networking and state-serialization examples.
- `docs/dummy_mode.md` — how dummy dispatch works and when `@enabledummy` is warranted.
- `docs/partial_compliance.md` — `@feature_unavailable`: how a driver whose hardware can't do
  everything its category declares stays constructible and introspectable.
- `docs/superreturn.md` — how drivers hand parsed values up to their category class.
- `docs/networking_data_paths.md` — RPC vs DataBank: which channel bulk data should take, and why
  binary on the RPC path is base64.
- `todo_list.md` — the live list of known bugs, open design questions, and remaining cleanup work.
  Worth checking before starting anything; several open items are traps rather than tasks.
