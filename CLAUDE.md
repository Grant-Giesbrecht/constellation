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
- Run tests: `pytest tests/` (uses plain `pytest`, no config file/markers — tests are simple `assert`-based
  functions in `tests/test_base.py`). Run a single test: `pytest tests/test_base.py::test_interpret_range`.
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
   tracking in the base class runs uniformly. Drivers set `self._super_hint` to communicate a parsed value
   up to the superclass's state-update logic.
3. **Mixins** (e.g. `MeasurementsMixin` in `oscilloscope_ctg.py`): optional capabilities not all drivers of
   a category support. A mixin declares `__state_key__` (name in `state.state_fragments`) and
   `__state_fragment__` (its `InstrumentState` subclass); `Driver.discover_mixins()` walks the MRO at
   `__init__` time and auto-registers any mixins the concrete driver class inherits from.

New drivers should only need to translate SCPI commands in the driver file — the category class and
`Driver` base handle state tracking, logging, online-status checks, and dummy-mode plumbing.

### State tracking (`src/constellation/base.py`)

- `InstrumentState` (Serializable, from `jarnsaxa`) holds all tracked parameters for a driver/category.
  Subclasses call `self.add_param(name, unit=..., value=...)` in `__init__` and list every param name in
  `__state_fields__`; `validate()` cross-checks the two stay in sync and warns (via `self.log`) if not.
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
over the network, or (via `dummy=True` on `Driver`) a simulated instrument with no relay activity at all —
dummy mode short-circuits `write`/`read`/`query` and instead routes through each driver's own
`dummy_responder(func_name, *args, **kwargs)` (pattern-matched on `set_*`/`get_*` prefixes) so code can be
developed and tested without physical instruments attached.

### Networking (`src/constellation/networking/`)

Built on the external `pyfrost` package (`GenCommand`, `Packable`, client/server). `NetworkCommand` wraps a
remote method call (`target_client`, `remote_id`/`remote_addr`, `function`/`args`/`kwargs`) so a client can
invoke driver methods on an instrument attached to a different host. `net_client.py`/`net_server.py` build
on `network.py`'s primitives to implement the actual client/server roles.

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
