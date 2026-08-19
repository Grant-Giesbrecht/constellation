# Constellation stabilization TODO

Working list from the state-tracking / dummy-mode / networking review (see also
`docs/dummy_and_state_review.md`, which covers the dummy+state bugs in more depth, and
`docs/labmesh_migration_plan.md`).

Status key: `[ ]` open, `[x]` done, `[~]` in progress, `[-]` considered and rejected.

Confidence key:
- **confirmed** — reproduced by running the code.
- **static** — read from the source; unambiguous, but not executed.

Test env note: the repo's deps (`pylogfile`, `stardust`, `labmesh`) are installed under
`/Library/Frameworks/Python.framework/Versions/3.14/bin/python3`, NOT under `~/Venv/.ve_main`.
Run tests with that interpreter: `.../3.14/bin/python3 -m pytest tests/ -q`
(baseline when this list was written: 34 passed, 10 xfailed; after the P1/P2 pass: 58 passed, 9 xfailed;
after the P3 pass: **87 passed, 5 xfailed**).

---

## Priority 1 — relay layer (data loss on live hardware)

- [x] **`DirectSCPIRelay.read()` discards the value it read.** `relay.py` — reads into `rv`,
      then `return True, ""`. Every caller of `Driver.read()` gets an empty string.
      *static*
- [x] **`VICPDirectSCPIRelay.read()` discards the value it read.** Same bug, same shape.
      *static*
- [x] **`VICPDirectSCPIRelay.query()` discards the value it read.** Same bug. Since `query()` is
      how every getter reads hardware, **the entire LeCroy/VICP path returns empty strings for
      everything.** Highest-impact bug in the relay layer. (`DirectSCPIRelay.query()` correctly
      returns `rv`, which is why the Rigol path works and this went unnoticed.)
      *static*
- [x] **`VICPDirectSCPIRelay.__init__` sets `self.instr = None`** but every other method uses
      `self.inst`. Dead attribute; `self.inst` is undefined until `connect()` succeeds.
      *static*

## Priority 2 — relay= plumbing (defeats the CommandRelay abstraction)

- [x] **Drivers that hard-code their relay cannot be networked.** `RigolDP832`,
      `SiglentSDM3000X`, `Keysight34400`, `Keithley2700`, `RohdeSchwarzZVA`, `SiglentSDG2000X`
      all pass `relay=DirectSCPIRelay()` inside `super().__init__()` and expose no `relay`
      parameter. Passing `relay=RemoteTextCommandRelayClient(...)` raises
      `TypeError: got multiple values for keyword argument 'relay'`. Only the two Rigol scopes
      and the Siglent SSA can go over the mesh today.
      *static*
- [x] **Shared mutable default relay.** `RigolDS1000Z`, `RigolDS1000E`, `SiglentSSA3000X` declare
      `relay:CommandRelay=DirectSCPIRelay()` as a signature default — evaluated once at import,
      so every instance built without an explicit `relay=` shares one relay object. Two scopes in
      one script silently talk to the same instrument.
      *confirmed — `docs/dummy_and_state_review.md` bug #4, `test_relay_default_argument_is_not_shared_between_instances`*
- [x] Fix both of the above with one signature convention everywhere:
      `def __init__(self, address, log, relay:CommandRelay=None, **kwargs)` +
      `relay = relay if relay is not None else DirectSCPIRelay()` in the body.
- [x] **`RohdeSchwarzZVA.__init__` swallows `**kwargs`** without forwarding to `super()`, so
      `dummy=True` and every other kwarg is silently ignored.
      *static*

### Found while fixing P1/P2

- [x] **`SpectrumAnalyzer.__init__` never passed `state` to `Driver.__init__`** — it assigned
      `self.state` *after* calling super(), but `Driver.__init__` requires `state` positionally and
      hands it to `discover_mixins()`. Every SpectrumAnalyzer driver raised
      `TypeError: Driver.__init__() missing 1 required positional argument: 'state'` and was
      impossible to construct. `SiglentSSA3000X` and `RohdeSchwarzFSE` now instantiate for the
      first time. *confirmed*
- [ ] **`RigolDS1000E` cannot be instantiated** — doesn't implement 16 of `Oscilloscope`'s abstract
      methods (`set_coupling`, `get_coupling`, trigger mode/level/source, probe attenuation,
      bandwidth limit, run/stop acquisition, single/force trigger). Pre-existing; the driver is
      half-migrated. *confirmed*
- [x] Removed the now-obsolete `xfail(strict=True)` on
      `test_relay_default_argument_is_not_shared_between_instances` (it XPASSed once P2 landed).
- [x] Added regression tests: relay `read()`/`query()` return values for both relay types, plus
      parametrized `relay=` injection and no-shared-default coverage across all 9 constructible
      drivers. Suite: **58 passed, 9 xfailed** (was 34 passed, 10 xfailed).

## Priority 3 — dummy mode: collapse to one mechanism — **DONE**

Design decision (agreed): delete `@enabledummy` from setters entirely, make `modify_state()` the
single dummy dispatch point, and reserve `dummy_responder()` for genuinely synthetic behavior only.

- [x] **Remove `@enabledummy` from every `set_*` category method.** They then flow through
      `modify_state()`, whose `self.dummy` branch already handles dummy correctly and generically.
- [x] **Add a generic dummy getter path to `modify_state()`** so `get_*` reads its own state path
      back instead of needing a hand-written `dummy_responder` case:
      `if self.dummy and query_func is None and value is None: return self.state.get(params, indices=indices, fragment=fragment)`
- [x] **Reduce `dummy_responder` to synthetic generators only** — `get_waveform`,
      `get_measured_output`, `get_trace_data`. Deletes the bulk of every category's
      `match func_name:` table.
- [x] **Fixes bug: 5 setters silently no-op in dummy mode.** `set_trigger_mode`,
      `set_trigger_level`, `set_trigger_source`, `set_probe_attenuation`, `set_bandwidth_limit`
      are `@enabledummy` but have no `dummy_responder` case, so they hit the generic fallback
      (`return -1`) and never touch `self.state`.
      *confirmed — `docs/dummy_and_state_review.md` bug #1*
- [x] **Fixes bug: mixin methods have no dummy support at all.** `add_measurement`,
      `clear_measurements`, `get_measurement`, `set_measurement_stat_display` are `@enabledummy`
      with no cases; `add_measurement(1, MEAS_VPP)` returns `None` instead of `True` and adds
      nothing to state. (There are two `#TODO: Handle dummy!` comments marking this.)
      *confirmed*
- [x] **Inverted fallback convention.** `Driver.dummy_responder` returns `None` for unrecognized
      `set_*` and `-1` for `get_*`; the `Oscilloscope`/`PowerSupply` overrides do the opposite.
      Should be moot once the above lands, but pick one convention.
      *confirmed — `docs/dummy_and_state_review.md` minor notes*

### Outcome

`modify_state()` is now the single dummy dispatch point. Two branches do all the work:
- **Setter in dummy** (`query_func` is not None): the pre-existing `self.dummy` branch stores the
  passed value. Unchanged.
- **Getter in dummy** (`query_func is None`): *new* branch reads the tracked value back via
  `self.state.get(params, indices, fragment)` instead of writing `self._super_hint` (which is
  `None` in dummy, since the driver's SCPI body never ran) into state.

That second branch is what removes the need for a hand-written `dummy_responder` case per getter.

- **44 `@enabledummy` decorators removed**; 7 remain, all genuinely synthetic or pure actions:
  `Oscilloscope.get_waveform` + the 4 acquisition actions, `PowerSupply.get_measured_output`,
  `BasicVectorNetworkAnalyzerCtg.get_trace_data`.
- **3 `dummy_responder` overrides deleted outright** (`ArbitraryWaveformGenerator`,
  `DigitalMultimeter`, `SpectrumAnalyzer`) — every case in them was literally
  `self.state.get(<same path>)`, i.e. exactly what the generic branch now does.
- **2 `dummy_responder` overrides shrunk to synthetic-only**: Oscilloscope 65 -> 31 lines,
  PowerSupply 53 -> 22 lines. Both now delegate unknown names to `super().dummy_responder()`,
  which also resolves the inverted set_/get_ fallback convention (one convention, in the base).
- Net **-178 lines** across the category classes.
- `InstrumentState.get()` gained `fragment=` (P5 item, pulled forward — the fragment read-back
  path needs it).
- `MeasurementsMixin.get_measurement` additionally fixed: now uses `populated_items()` instead of
  `enumerate()` (the positional-vs-key bug from P10), reports the correct message when a
  measurement isn't found, uses `self.warning()` rather than `self.log.warning()`, and gains a
  `_dummy_measurement()` helper that computes VMAX/VMIN/VPP/VAVG/FREQ from the driver's own dummy
  waveform — so dummy measurements agree with what `get_waveform()` returns instead of being a
  sentinel.

Verified: the 5 previously-silent setters (`set_trigger_mode`, `set_trigger_level`,
`set_trigger_source`, `set_probe_attenuation`, `set_bandwidth_limit`) now round-trip through
state; `add_measurement`/`clear_measurements`/`get_measurement`/stat-display work in dummy mode
for the first time. 4 more `xfail(strict=True)` markers XPASSed and were removed.

Tests: **87 passed, 5 xfailed** (from 58/9). New coverage includes a guard-rail test asserting the
exact set of methods still allowed to carry `@enabledummy` — it fails if someone decorates a plain
setter again, which is the mistake that caused the original bug. `osc_dummy_demo.py`,
`psu_dummy_demo.py`, `dmm_dummy_demo.py` and `state_tracker_demo.py` all still run clean.

### Follow-ups noticed during this pass

- [ ] **`DigitalMultimeter.dummy_responder`'s deleted `return_selected()` helper referenced
      `self.state.result_B`**, but the state field is `result_R` — it would have raised
      `AttributeError` for any resistance measurement. Moot now (the override is gone), but
      `get_value()` still has no synthetic dummy reading: in dummy it reads `result_I`/`result_V`/
      `result_R` back, which are `None` until something sets them. Decide whether a DMM should
      synthesize a reading the way `PowerSupply.get_measured_output` does. *confirmed*
- [ ] **`SpectrumAnalyzer` and VNA have no `init_dummy_state()` content** (`pass`), so their dummy
      state starts entirely `None` and getters read `None` back. Now that read-back is the generic
      path, populating a sensible default state matters more than it did. *static*

## Priority 4 — `_super_hint` and `superreturn`

- [ ] **`superreturn` uses `super(type(self), self)`** — `type(self)` is the runtime class, not the
      defining class. Works today only because no concrete driver is subclassed; the first
      `class MyScope(RigolDS1000Z)` infinite-recurses on every decorated method.
      Fix: make `superreturn` a descriptor class and capture the owner in `__set_name__` — that
      is the only hook that sees the *defining* class, and only descriptors (not plain functions)
      receive it:

      ```python
      class superreturn:
          def __init__(self, func):
              self.func = func; self.owner = None
              functools.update_wrapper(self, func)
          def __set_name__(self, owner, name):
              self.owner = owner                      # e.g. RigolDS1000Z, captured once
          def __get__(self, obj, objtype=None):
              return self if obj is None else functools.partial(self.__call__, obj)
          def __call__(self, obj, *args, **kwargs):
              obj._super_hint = None                  # fixes the stale-value bug too
              if not obj.dummy:
                  try:
                      obj._super_hint = self.func(obj, *args, **kwargs)   # capture the return
                  except Exception as e:
                      obj.log.error(f"Failed to call driver function: >:a{self.func}< ({e}).")
                      return None
              return getattr(super(self.owner, obj), self.func.__name__)(*args, **kwargs)
      ```

      `self.owner` is fixed at class-creation time regardless of `type(obj)`, so the MRO walk
      always advances and terminates. This single rewrite folds in all four P4 items at once.
      *static (well-known Python failure mode)*
- [ ] **`_super_hint` is never cleared between calls.** A driver getter that early-`return`s
      (e.g. `RigolDS1000Z.get_coupling` on an unrecognized coupling string) leaves the *previous*
      call's value in the slot, which the category then writes into state. Fix: clear at the top
      of `superreturn`'s wrapper.
      *static*
- [ ] **`_super_hint` is not re-entrancy safe** — a getter that calls another getter clobbers it.
      `digital_multimeter_ctg.py:129` already hand-works-around this
      (`local_super_hint = self._super_hint`), which is the tell.
      *static*
- [ ] **Have drivers `return` their parsed value** and let `superreturn` capture it into
      `_super_hint`, instead of each driver assigning the slot directly. Mechanical edit of
      ~147 sites (`self._super_hint = X` -> `return X`); category classes need no change.

## Priority 5 — `InstrumentState.set()` / `get()`

- [ ] **Unify behind one private `_resolve(params, indices, fragment)` helper.** `set()` and
      `get()` are copy-paste twins that have already drifted; a shared resolver makes further
      drift structurally impossible.
- [ ] **`set(..., fragment=X)` raises `UnboundLocalError` for an unknown fragment** — the error
      message interpolates `obj_top` before it's assigned, instead of logging and returning `False`.
      *confirmed — `docs/dummy_and_state_review.md` bug #2*
- [x] **`get()` has no `fragment=` parameter** at all, unlike `set()`. (Done early — the P3
      dummy read-back path required it. The full `_resolve()` unification is still open.) No way to read a
      state-fragment value back through the top-level API.
      *confirmed — `docs/dummy_and_state_review.md` bug #3*
- [ ] Both leave `list_at_top` unbound if `params` is empty, and both reference the loop variable
      `idx` after the loop body.
      *static*

## Priority 6 — IndexedList

Keep the class — sparse allocation, arbitrary base index, and `"idx-N"` string keys for HDF5
survival all earn their place (all three verified). These are cleanups and bugs, not a redesign.

- [ ] **`validate_type` does not survive serialization.** Left out of `__state_fields__` (existing
      TODO). After a round trip the attribute is *gone*, so `__setitem__`/`set_idx_val` raise
      `AttributeError: 'IndexedList' object has no attribute 'validate_type'`. A restored
      IndexedList is write-broken. Doesn't affect `state.set(["channels","div_volt"], ...)` (that
      path `setattr`s the channel object and never calls `__setitem__`) but **does** break
      `append()`, hence `MeasurementsMixin.add_measurement` after any `restore_state()`.
      Fix: store the type name as a string in `__state_fields__`, and `getattr`-guard the check.
      *confirmed*
- [ ] **`summarize()` assumes every value is an `InstrumentState`** — calls `.state_str()`
      unconditionally, so an IndexedList of plain scalars raises
      `AttributeError: 'float' object has no attribute 'state_str'`. Nothing stores scalars in one
      today, but `validate_type` is optional so nothing prevents it.
      *confirmed*
- [ ] **`append(value, allow_expand=False)`** — `allow_expand` is accepted and ignored (TODO in
      body); still returns `False` past capacity even with `allow_expand=True`. Implement or drop
      the parameter.
      *confirmed*
- [ ] **Collapse duplicate method pairs.** `get_idx_val`/`__getitem__` and
      `set_idx_val`/`__setitem__` are independent reimplementations that happen to be behaviorally
      identical (verified: same value populated, same `None` unpopulated, same `KeyError` out of
      range). Make one pair a thin alias for the other so they can't drift.
- [ ] `get_valid_idx()`'s docstring says "zero-indexed" but the whole point of `first_index` is
      that it needn't be. Same wording in `set_idx_val`/`get_idx_val`/`idx_is_populated`.
      *static*
- [ ] Consider a `ChannelList` alias for readability at channel-shaped call sites.

## Priority 7 — `add_param` / `__state_fields__` / `validate()`

Conclusion: the two-list system is at its floor. `__state_fields__` is stardust's class-level
serialization manifest; `add_param` is Constellation's per-instance units/is_data registry.
Deriving one from the other needs class-creation-time info that `add_param` doesn't have.
`validate()` is the right guard — the problem is it isn't reliably *called*.

### How to enforce `validate()` automatically (prototyped and verified)

Calling it from `Driver.__init__`/`discover_mixins()` was the first idea, but it's the wrong
level: it only reaches `self.state` and the mixin fragments, and misses every *nested*
`InstrumentState` (the per-channel/per-trace objects inside an `IndexedList`, and any state class
built lazily — `OscilloscopeMeasurementSetting` is only constructed inside `add_measurement()`,
long after `Driver.__init__` has returned).

The enforcement point that can't be missed is `InstrumentState.__init_subclass__`, which fires
once per subclass *definition*. It can't call `validate()` itself (there's no instance yet), but it
can wrap the subclass's `__init__` so validation runs automatically right after construction:

```python
def __init_subclass__(cls, **kwargs):
    super().__init_subclass__(**kwargs)      # MUST cooperate: Serializable uses this hook too,
                                             # for class registration + __state_fields__ merging
    orig_init = cls.__init__                 # may already be a wrapper (inherited) - wrap anyway
    @functools.wraps(orig_init)
    def _validating_init(self, *args, **kw):
        orig_init(self, *args, **kw)
        if type(self) is cls:                # only the most-derived class fires -> exactly once
            self.validate()
    cls.__init__ = _validating_init
```

Verified behavior:
- **Fires exactly once per construction** in all three inheritance shapes — a base class, a
  subclass that defines its own `__init__`, and a subclass that *inherits* `__init__`. (The
  `type(self) is cls` guard is what prevents a `B(A)` hierarchy from validating twice. Note the
  obvious-looking optimization of tagging the wrapper and skipping already-wrapped `__init__`s is
  **wrong** — a subclass that inherits `__init__` would then never validate at all.)
- **Catches the real drift**: constructing `OscilloscopeMeasurementSetting` under the hook
  immediately reports `last_measured_value` as missing from `__state_fields__`.
- **Cooperates with `Serializable.__init_subclass__`** (class registration and the
  `__extend_state_fields__` parent-field merge) as long as `super().__init_subclass__(**kwargs)`
  is called first.
- Constructing all five working drivers under the hook produces **zero** new warnings, so turning
  this on is not a noise event — the only thing it surfaces today is the one genuine bug.

Once this is in, delete the ~8 manual `self.validate()` calls scattered through the category
classes; they become redundant (and can't be forgotten by the next state class).

- [-] ~~Call `validate()` centrally from `Driver.__init__`/`discover_mixins()`~~ — superseded by
      the `__init_subclass__` approach above; the `Driver.__init__` level can't see nested or
      lazily-built state objects.
- [ ] **Implement the `__init_subclass__` auto-validate hook** in `InstrumentState` (needs
      `import functools` in `base.py`).
- [ ] **Remove the now-redundant manual `self.validate()` calls** from the category state classes.
- [ ] **Do the `validate()` output fix in the same pass** (see below) — auto-validation makes a
      `print()`-to-stdout side effect fire on every state object ever constructed, which is much
      worse than it is today.
- [ ] **`OscilloscopeMeasurementSetting` has real drift**: `add_param("last_measured_value", ...)`
      but it's missing from `__state_fields__`, and the class never calls `validate()` — so the
      field silently does not serialize.
      *confirmed*
- [ ] `OscilloscopeMeasurementMixinState` never calls `validate()`.
      *confirmed*
- [ ] `BasicVectorNetworkAnalyzerState` never calls `validate()`.
      *confirmed*
- [ ] **`validate()` `print()`s to stdout** with colorama in addition to logging. Wrong for a
      library — route through `self.log` only.
      *static*

## Priority 8 — networking (labmesh)

### pyfrost removal: source is clean, docs are stale

Verified: **no file under `src/` imports `pyfrost`, `GenCommand`, `Packable`, `NetworkCommand`,
`net_client`, or `net_server`.** `pyproject.toml` has `labmesh` + `pyzmq` and no
`pyfrost-network`. The migration in `docs/labmesh_migration_plan.md` is genuinely complete.
What remains is documentation and detritus:

- [ ] **`CLAUDE.md` "Networking" section still describes the pyfrost design** and names three
      files that no longer exist (`network.py`, `net_client.py`, `net_server.py`). Actively
      harmful — it's the file that seeds agent context every session. Rewrite to describe
      `relay.py`'s `RemoteTextCommandRelayClient`/`Listener` and
      `networking/labmesh_net.py`'s `DriverStateBroadcaster`.
- [ ] **`README.md`** — stale line `- Mention PyFrost (WIP)`.
- [ ] **`oscilloscope_ctg.py`** — commented-out
      `# from constellation.networking.net_client import NetworkCommand, NetworkReply`.
- [ ] **Stale bytecode**: `src/constellation/networking/__pycache__/` still contains
      `net_client.cpython-314.pyc`, `net_server.cpython-314.pyc`, `network.cpython-314.pyc` for
      deleted modules. Delete; consider a `.gitignore` entry.

### Networking bugs

- [ ] **`query_binary` does not cross the network — waveform capture over labmesh silently
      returns empty data.** `RemoteTextCommandRelayClient` doesn't override `query_binary`, so the
      base raises `NotImplementedError`; `Driver.query_binary` catches it, logs, and returns `[]`.
      `RigolDS1000Z.get_waveform(binary=True)` is the *default* path and none of the networking
      examples pass `binary=False`. Biggest functional gap in the networking layer.
      Fix: base64 the block in `RemoteTextCommandRelayListener.query_binary`, decode client-side
      (the listener already returns lists-not-tuples for JSON's sake, so the convention exists).
      *static*
- [ ] **`RemoteTextCommandRelayClient.close()` leaks the event-loop thread.** Nulls `director`
      and `relay_client` but never stops `self._loop` or joins `_loop_thread`. Daemon threads mean
      the process still exits, but open/close cycles accumulate loops. Add
      `self._loop.call_soon_threadsafe(self._loop.stop)`.
      *static*
- [ ] **No reconnection path.** `Driver.check_online()`'s AUTO branch queries `*IDN?`; over a
      network relay a transient broker/relay hiccup marks the driver offline permanently, and
      `Driver.write`/`query` then early-return on `if not self.online`. Nothing recovers short of
      a manual `connect()`. Needs a retry/reconnect policy — the whole point of the mesh is
      long-running unattended nodes.
      *static*
- [ ] **`Driver.connect()` references an undefined `e`** — the failure `else:` branch
      f-string interpolates `{e}` with no `except` in scope, so reporting a connection failure
      raises `NameError`. Hits the networking path hardest since that's where connects fail.
      *static*
- [ ] **`Driver.check_online()`'s AUTO branch doesn't skip the query for non-SCPI instruments** —
      warns and sets `online = False`, but no `return`/`elif`, so it falls through to an
      unconditional `relay.query("*IDN?")` that immediately overwrites `online`.
      *confirmed — `docs/dummy_and_state_review.md` bug #5*

### Networking optimizations

- [ ] **Batch round-trips.** Every `Driver.query()` is a synchronous mesh round-trip via
      `asyncio.run_coroutine_threadsafe(...).result()`. `refresh_state()` on a 4-channel scope is
      ~15 sequential round-trips. Add a `query_many(cmds: list) -> list` RPC to the listener and
      use it from category `refresh_state()` where the driver supports it.
- [ ] **Deduplicate the example sets.** `examples/osc_networking/` and
      `examples/rigol_ds1000z_network/` are near-identical five-file sets
      (broker / bank / relay node / client / observer). Keeping both in sync is a tax — delete one.

## Priority 9 — wrong-name calls and signature mismatches

All of these are methods calling names that do not exist, or passing wrong argument shapes.

- [ ] **`PowerSupply.set_current` and `set_output_enable` pass the wrong readback query** —
      both use `lambda: self.get_voltage(channel)`. On real hardware, setting current queries
      *voltage* back: `current_set`/`enable` never update and `voltage_set` is rewritten instead.
      *static*
- [ ] **`PowerSupply.apply_state` calls `self.set_enable_output(...)`** — no such method
      (it's `set_output_enable`). The surrounding `try/except` swallows it as a `lowdebug`, so
      **apply_state silently fails to restore output enable on every channel, with no error.**
      *static*
- [ ] **`PowerSupply.refresh_data` calls `self.get_output_measurement(ch)`** — no such method
      (it's `get_measured_output`). Uncaught `AttributeError`.
      *static*
- [ ] **`PowerSupply.dummy_responder` has `case "set_enable"`** — no method produces that name.
      *static*
- [ ] **`SpectrumAnalyzer.refresh_state`/`apply_state` call `get_num_points()`/`set_num_points()`**
      which are commented out in the same file -> `AttributeError`.
      *static*
- [ ] **`SpectrumAnalyzer.refresh_state`/`refresh_data` call `self.get_trace_data(self, t_idx)`** —
      passing `self` as the first positional argument.
      *static*
- [ ] **`Oscilloscope.apply_state` can't restore trigger source.** Calls
      `set_trigger_source(self.state.trigger_source)`, but the signature is
      `(channel:int=None, external:bool=False, line:bool=False)` and the stored value is a string
      like `"CHAN1"` -> `_format_trigger_source` does `"CHAN1" < 1` -> `TypeError`. Round-tripping
      a saved scope state fails here.
      *static*
- [ ] **`get_all_waveforms` breaks the DS1000E.** It passes `_skip_run_management=True`, but
      `RigolDS1000E.get_waveform(self, channel)` takes no kwargs. `superreturn`'s `except` catches
      the `TypeError`, so `get_all_waveforms()` returns a list of `None`s and logs an error rather
      than raising.
      *static*

## Priority 10 — mixins

- [ ] **`refresh_mixins()` / `apply_mixins()` are dead code.** Both iterate
      `self.state.state_fragments.values()` looking for `refresh_state`/`apply_state`, but those
      are defined on `MeasurementsMixin` (the *driver* mixin), not on
      `OscilloscopeMeasurementMixinState` (the *fragment*). No fragment has them, so both loops
      always no-op.
      *static*
- [ ] **MRO shadowing hazard.** Because `MeasurementsMixin.refresh_state` is in the driver's MRO,
      a driver declared `class X(MeasurementsMixin, Oscilloscope)` (rather than
      `class X(Oscilloscope, MeasurementsMixin)`) silently gets the mixin's `pass` as its entire
      `refresh_state`. Either move the hooks onto the fragment classes, or have `refresh_mixins`
      walk the MRO the way `discover_mixins()` does.
      *static*
- [x] **`MeasurementsMixin.get_measurement` confuses positional index with IndexedList key.**
      `for idx, am in enumerate(...active_measurements)` then `active_measurements[meas_idx]` —
      but `__iter__` skips unpopulated slots, so `idx` is a positional counter. Agrees today only
      because `first_index=0` and `append()` fills densely; any gap (e.g. `clear_measurements()`
      then selective re-add) silently updates the wrong measurement. Use `populated_items()`.
      *static*
- [x] **`MeasurementsMixin` uses `self.log.warning(...)`** instead of the `Driver.warning()`
      wrapper, so its messages lack the instrument identifier prefix (`CLAUDE.md` requires the
      wrappers).
      *static*
- [x] **Wrong message text** in `get_measurement`'s not-found branch: logs "Measurement already
      exists" when the measurement was *not* found.
      *static*

## Priority 11 — data-shape consistency (design work needed)

- [ ] **Waveform dict key casing is inconsistent.** `OscilloscopeChannelState`'s default value is
      `{"time_S": [], "volt_V": []}` (capital S) but `remake_dummy_waves()` and the real
      `get_waveform()` both write `"time_s"`. `_waveform_xdata()` looks for `"time_s"`, so the
      declared default is dead — and it's a trap for the next driver author.
      *static*
- [ ] **Dummy waveforms have a different shape than real ones.** `remake_dummy_waves()` omits the
      `"channel"` key that real `get_waveform()` includes, and mixes `numpy.ndarray` /
      `numpy.float64` into an otherwise plain dict.
      *confirmed — `docs/dummy_and_state_review.md` minor notes*
- [ ] **Design a shared x/y-with-units contract** for waveform/trace/spectrum data, plus unit
      conversion. Decision from discussion: **no new class** — the root problem is that the unit
      is encoded in the *key name* (`time_s` vs `time_mS`), which is why it's unenforceable and
      unconvertible. Move the unit into a value and the keys become fixed:

      ```python
      {"x": [...], "y": [...], "x_unit": "s", "y_unit": "V"}
      ```

      Two pieces make it enforceable rather than conventional:
      1. `normalize_xy(data, ...)` — accepts the legacy shapes (`{"time_s":..., "volt_V":...}`,
         `{"time_idx":...}`) and returns the canonical form, logging a deprecation. Called at the
         `modify_state` boundary, the same choke point that makes dummy mode work, so drivers can
         be migrated one at a time instead of all at once.
      2. `convert(values, from_unit, to_unit)` — ~25 lines over an SI-prefix table
         (`f/p/n/u/m/''/k/M/G/T`), splitting `"mS"` into prefix+base and rejecting mismatched base
         units. Then `convert(wav["x"], wav["x_unit"], "us")` works regardless of which driver
         produced the waveform, which is the actual goal.

      Deliberately **not** `pint`: `Quantity` objects don't round-trip through HDF5/JSON, so they'd
      fight `InstrumentState` serialization at every boundary. Plain floats + a unit *string* stay
      serializable — the same constraint that shaped `IndexedList`'s `"idx-N"` keys.

      Note this is a **breaking change** to the waveform contract: `plot_waveform()`, the GUI
      widgets (`ui.py`, `oscilloscope_gui.py`), and the networking examples all read
      `time_s`/`volt_V` today. Needs its own pass, not a fold-in.

## Priority 12 — state persistence

- [ ] **`state_to_dict(include_data=False)`'s `include_data` parameter is dead** — never
      referenced in the body. `self.data` is never included regardless, contradicting the
      docstring. Decide what it should mean (waveforms aren't small — does data belong in the
      same HDF file, or in the labmesh databank?) and implement or remove.
      *confirmed — `docs/dummy_and_state_review.md` bug #6*
- [ ] **`channel_colors` (a plain `dict[int, tuple]`) is silently lost by `dump_state()`.**
      h5py can't create groups with non-string names; the failure is swallowed by
      `stardust.dict_to_hdf` and `dump_state()` still returns `True`. This is the exact problem
      `IndexedList`'s `"idx-N"` convention solves — it just wasn't applied here.
      *confirmed — `docs/dummy_and_state_review.md` bug #7*
- [ ] **Enforce string-keyed dicts structurally**: have `add_param()` reject/warn on dict values
      with non-string keys, so this can't recur.
- [ ] **Restored values come back as numpy scalars**, not native Python types (`bool` ->
      `numpy.bool_`, `float` -> `numpy.float64`, `int` -> `numpy.int64`). Either cast back on
      reconstruction or document as an accepted permanent limitation — currently it's neither.
      *confirmed — `docs/dummy_and_state_review.md` bug #8*
- [ ] **Upstream (`stardust` repo, not here): `dict_to_hdf`'s success/failure contract is
      unreliable.** `write_level()` discards its own recursive calls' return values, and the
      top-level function returns `True` even on its explicit failure branch. Until fixed,
      `Driver.dump_state()`'s "Returns True if successfully saved" promise does not hold.

## Priority 13 — minor cleanups

- [ ] `Driver.__init__` sets `self.dummy = False` then `self.dummy = dummy` a dozen lines later.
      Dead assignment.
      *confirmed — `docs/dummy_and_state_review.md` minor notes*
- [ ] `Driver.read()`'s offline warning says "Cannot write when offline" (copy-paste from
      `write()`).
      *static*
- [ ] `InstrumentState.surpress_warnings` — spelling (`suppress`).
      *static*
- [ ] `Identifier.__repr__` is multi-line (there's a TODO noting it nests badly inside other
      reprs).
      *static*
- [ ] `IndexedList` has a TODO to validate that stored values are JSON-serializable.
      *static*

---

## Execution order (agreed)

1. Relay return-value bugs (P1) — one line each; one of them means an entire instrument family
   returns nothing.
2. `relay=` signature normalization (P2) — fixes the shared-mutable-default *and* the
   can't-be-networked bug in one pass.
3. Collapse dummy dispatch into `modify_state` (P3) — largest available simplification; fixes the
   silently-no-op setters and the mixin dummy gap.
4. `_super_hint` -> return value, and the `superreturn` descriptor fix (P4) — same eight lines,
   cheaper together.
5. Unify `state.set`/`state.get` behind one resolver, add `fragment=` to `get` (P5).
6. Wrong-name calls in `PowerSupply` / `SpectrumAnalyzer` / `Oscilloscope.apply_state` (P9).
7. `query_binary` over the mesh (P8), then update `CLAUDE.md` / `README.md`.
