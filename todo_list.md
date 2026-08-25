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
after the P3 pass: 87 passed, 5 xfailed;
after the P4 pass: 95 passed, 5 xfailed;
after the P5 pass: 112 passed, 4 xfailed;
after the P9 pass: 124 passed, 4 xfailed;
after the P8 pass: 135 passed, 4 xfailed;
after write_binary: 150 passed, 4 xfailed;
after the P6 pass: 160 passed, 4 xfailed;
after the P2 partial-compliance pass: 172 passed, 4 xfailed;
after the P7 auto-validate pass: 181 passed, 4 xfailed;
after the P8 bug batch: 189 passed, 3 xfailed;
after ReconnectPolicy: 200 passed, 3 xfailed;
after error classification: **216 passed, 3 xfailed**).

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
      bandwidth limit, run/stop acquisition, single/force trigger). *confirmed*

      **CORRECTION (owner, 2026-08-20): this driver is NOT "half-migrated" and must not be
      "completed".** The DS1000E hardware has genuinely incomplete remote-access capability —
      some critical parameters cannot be read or set over SCPI at all. It exists both because the
      instrument is in active use, and deliberately as the worked example of *how Constellation
      represents an instrument that cannot be fully category-compliant*.

      So the task is not "implement the missing SCPI". It is: **decide and demonstrate the
      partial-compliance pattern**, then apply it here. Right now the driver is un-instantiable,
      which is the worst of both worlds — the unsupported methods are invisible until Python
      refuses to construct the class, and the useful 80% of the driver is unreachable.

      `FeatureUnavailable(RuntimeError)` already exists in `base.py` for exactly this and is
      **never raised anywhere** — it looks like the intended mechanism, abandoned before use.

      - [x] Decide the pattern. **Chosen: the leading candidate.** The driver overrides every
            abstract method, and the genuinely impossible ones are marked
            `@feature_unavailable("<why>")`, which raises `FeatureUnavailable` naming the method
            and the hardware limitation. Class becomes constructible; the supported majority
            stays usable; the failure moves from construction time to the exact unsupported call.
      - [x] Declarative form implemented, so capability is introspectable *before* calling:
            `Driver.feature_is_available(name)` and `Driver.unavailable_features()` (name ->
            reason). Both read the class via `inspect.getattr_static`, so they neither bind
            methods nor trigger properties, and work without a connection.
      - [x] `refresh_state()`/`apply_state()`/`refresh_data()`/`init_dummy_state()` now skip
            unavailable features and log at debug instead of aborting the sweep. Implemented in
            `Driver._wrap_state_sweeps()`, called once from `Driver.__init__`, which wraps the
            *instance's* bound methods in a `_state_sweep_depth` counter that
            `@feature_unavailable` consults. Wrapped centrally rather than per-category because
            all four are abstract on `Driver` and every category (including future ones) writes
            its own. Instance-level shadowing keeps `super().refresh_state()` chains working and
            un-double-wrapped; the counter unwinds in a `finally`.
      - [x] Applied to `RigolDS1000E`; it is constructible for the first time.
      - [x] Documented in `docs/partial_compliance.md` (with mermaid diagrams) and pointed to
            from `CLAUDE.md`.
      - [ ] **Verify the 16 "unimplemented" methods against the hardware.** `set/get_coupling`,
            `set/get_probe_attenuation`, `set/get_bandwidth_limit`, `set/get_trigger_mode`,
            `set/get_trigger_level`, `set/get_trigger_source`, `run_acquisition`,
            `stop_acquisition`, `do_single_trigger`, `do_force_trigger` are currently marked
            `@feature_unavailable` with the reason *"not yet implemented — SCPI support
            unverified on hardware"*, deliberately worded differently from the timebase entries.
            They were never established as hardware limitations — they simply had no DS1000E
            implementation, and marking them was what made the class constructible. The DS1000E
            programming guide appears to document commands for most of them. **Needs a bench
            check by the owner**, since it's a hardware fact, not something readable from the
            source. Converting one is a single-method edit: delete the decorator, add
            `@superreturn` and the SCPI body.
      - [ ] Replace the file's header link — it currently points at the DS1000**Z** programming
            guide, carried over when the file was copied from that driver.

      ### Found while implementing this

      - [x] `Oscilloscope.remake_dummy_waves()` raised `TypeError` on a scope with no timebase.
            `init_dummy_state()` seeds defaults through the setters, so with `set_div_time`
            unavailable `div_time`/`offset_time` stay `None` and `ndiv_horiz * None` blew up.
            Now falls back to a nominal timebase — which mirrors the hardware, since the real
            DS1000E driver returns sample index rather than seconds for the same reason.
      - [x] The old warn-and-continue stubs were actively harmful, not merely useless: with
            `@superreturn` they fell through to the category method, which wrote the requested
            value into `self.state`. The tracker reported a timebase the instrument had never
            been told about and `get_div_time()` handed it back as if read from hardware. A test
            (`test_unavailable_feature_does_not_write_state`) pins this.
      - [ ] **`FeatureUnavailable` is now raised, but nothing catches it.** Worth deciding
            whether any of the higher-level helpers (`get_all_waveforms`, the GUI widgets,
            `DriverStateBroadcaster`) should handle it specially, or whether the sweep
            suppression covers every case that matters. No evidence yet that it doesn't.
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

## Priority 4 — `_super_hint` and `superreturn` — **DONE**

- [x] **`superreturn` uses `super(type(self), self)`** — `type(self)` is the runtime class, not the
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
- [x] **`_super_hint` is never cleared between calls.** A driver getter that early-`return`s
      (e.g. `RigolDS1000Z.get_coupling` on an unrecognized coupling string) leaves the *previous*
      call's value in the slot, which the category then writes into state. Fix: clear at the top
      of `superreturn`'s wrapper.
      *static*
- [x] **`_super_hint` is not re-entrancy safe** — a getter that calls another getter clobbers it.
      `digital_multimeter_ctg.py:129` already hand-works-around this
      (`local_super_hint = self._super_hint`), which is the tell.
      *static*
- [x] **Have drivers `return` their parsed value** and let `superreturn` capture it into
      `_super_hint`, instead of each driver assigning the slot directly. Mechanical edit of
      ~147 sites (`self._super_hint = X` -> `return X`); category classes need no change.

### Outcome

`superreturn` is now a descriptor class. `__set_name__` captures the **defining** class once at
class-creation time, so the `super()` walk always advances. It also owns `_super_hint` end to end:
it clears the slot at the top of every call, and captures the driver's **return value** into it.

- **92 driver assignments converted** across 9 migrated drivers (`self._super_hint = X` ->
  `return X`), plus 6 more in the unmigrated `to_extended/LeCroy_WaveRunner44Xi_dvr.py` — left
  alone it would have been silently broken by the new capture, since the decorator now overwrites
  the slot with the function's return value.
- Category classes were **not** touched: they still read `self._super_hint`. Only the write side moved.
- All 92 sites were checked for tail position by AST before converting; 7 needed review and all 7
  were safe. One dead bare `return` in `RigolDS1000Z.get_bandwidth_limit` removed.

**Correction to the original bug description.** The recursion bug is real but its failure mode is
*not* a visible `RecursionError`. Measured on the pre-fix code with a subclassed driver: the call
recursed ~993 frames deep, then `superreturn`'s own `except Exception` **swallowed** the
`RecursionError` and returned `None`. So a subclassed driver silently returned `None` from every
decorated method and logged one error line, rather than crashing. After the fix the same call
makes exactly **1** query and returns the right value.

`DigitalMultimeter.get_value`'s `local_super_hint` workaround is still required and still correct —
it calls `get_measurement()` mid-body, which is itself `@superreturn`-wrapped and so clears and
rewrites the slot. Re-entrancy is now *safe* (each call gets a clean slot) but not *transparent*
(an inner call still overwrites the outer one's value), so saving it first remains necessary.

Tests: **95 passed, 5 xfailed** (from 87/5). New coverage exercises the real, non-dummy driver
bodies for the first time, via a `_CannedRelay` that replays canned SCPI: return-value capture,
value translation, the stale-hint regression, subclass-no-recursion (asserting a query count of 1,
which would have been ~1000), subclass state tracking, SCPI still being emitted, metadata
preservation, and a guard rail asserting no driver assigns `_super_hint` directly.

## Priority 5 — `InstrumentState.set()` / `get()` — **DONE**

- [x] **Unify behind one private `_resolve(params, indices, fragment)` helper.** `set()` and
      `get()` are copy-paste twins that have already drifted; a shared resolver makes further
      drift structurally impossible.
- [x] **`set(..., fragment=X)` raises `UnboundLocalError` for an unknown fragment** — the error
      message interpolates `obj_top` before it's assigned, instead of logging and returning `False`.
      *confirmed — `docs/dummy_and_state_review.md` bug #2*
- [x] **`get()` has no `fragment=` parameter** at all, unlike `set()`. (Done early — the P3
      dummy read-back path required it. The full `_resolve()` unification is still open.) No way to read a
      state-fragment value back through the top-level API.
      *confirmed — `docs/dummy_and_state_review.md` bug #3*
- [x] Both leave `list_at_top` unbound if `params` is empty, and both reference the loop variable
      `idx` after the loop body.
      *static*

### Outcome

`set()` and `get()` are now three-line wrappers over two shared helpers, `_resolve()` and
`_get_fragment()`. 123 lines of duplicated walk became 152 lines with the duplication gone (the
growth is docstrings and the error handling the old copies lacked). `_resolve()` returns a
`(container, key, is_indexed)` triple, so both callers agree by construction on which paths are
legal — the drift class that produced these bugs is now structurally impossible.

Behavior changes, each verified against the pre-change code rather than assumed:

| Case | Before | After |
|---|---|---|
| `set([], v)` / `get([])` — empty path | `UnboundLocalError` | `False` / `None`, logged |
| `set(..., fragment="nope")` | `UnboundLocalError` | `False`, logged |
| index out of range | raw `KeyError` propagated | `False` / `None`, logged |
| descending through an unpopulated slot | misleading "Parameter not found" | names the slot and index |
| value rejected by `IndexedList.validate_type` | `TypeError` propagated | `False`, logged |
| everything else | unchanged | unchanged |

The `KeyError`/`TypeError` → logged-return change makes this path have exactly one failure mode
instead of three. Checked that nothing depended on the exceptions propagating: there is no
`except KeyError` anywhere in `src/`, `examples/` or `tests/` other than the one now inside
`_resolve()` itself.

Also corrected `modify_state()`'s docstring, which claimed `indices` holds N-1 ints for N params.
It doesn't — the tuples are **parallel**: `indices[i]` applies to `params[i]` when that param is an
IndexedList. Demonstrated: `set(["channels","div_volt"], v, indices=[3])` (2 params, 1 index)
resolves correctly.

Tests: **112 passed, 4 xfailed** (from 95/5). One more `xfail(strict=True)` XPASSed and was
removed. New coverage: happy paths for scalar/indexed/fragment/whole-slot access, a parametrized
sweep of 8 invalid paths asserting neither method raises, state-left-untouched on failure,
unpopulated-element reporting, type-rejection, and `test_set_and_get_agree_on_what_is_a_valid_path`
which asserts the two methods accept exactly the same path set.

## Priority 6 — IndexedList — **DONE**

Keep the class — sparse allocation, arbitrary base index, and `"idx-N"` string keys for HDF5
survival all earn their place (all three verified). These are cleanups and bugs, not a redesign.

- [x] **`validate_type` does not survive serialization.** Left out of `__state_fields__` (existing
      TODO). After a round trip the attribute is *gone*, so `__setitem__`/`set_idx_val` raise
      `AttributeError: 'IndexedList' object has no attribute 'validate_type'`. A restored
      IndexedList is write-broken. Doesn't affect `state.set(["channels","div_volt"], ...)` (that
      path `setattr`s the channel object and never calls `__setitem__`) but **does** break
      `append()`, hence `MeasurementsMixin.add_measurement` after any `restore_state()`.
      Fix: store the type name as a string in `__state_fields__`, and `getattr`-guard the check.
      *confirmed*
- [x] **`summarize()` assumes every value is an `InstrumentState`** — calls `.state_str()`
      unconditionally, so an IndexedList of plain scalars raises
      `AttributeError: 'float' object has no attribute 'state_str'`. Nothing stores scalars in one
      today, but `validate_type` is optional so nothing prevents it.
      *confirmed*
- [x] **`append(value, allow_expand=False)`** — `allow_expand` is accepted and ignored (TODO in
      body); still returns `False` past capacity even with `allow_expand=True`. Implement or drop
      the parameter.
      *confirmed*
- [x] **Collapse duplicate method pairs.** `get_idx_val`/`__getitem__` and
      `set_idx_val`/`__setitem__` are independent reimplementations that happen to be behaviorally
      identical (verified: same value populated, same `None` unpopulated, same `KeyError` out of
      range). Make one pair a thin alias for the other so they can't drift.
- [x] `get_valid_idx()`'s docstring says "zero-indexed" but the whole point of `first_index` is
      that it needn't be. Same wording in `set_idx_val`/`get_idx_val`/`idx_is_populated`.
      *static*
- [x] Consider a `ChannelList` alias for readability at channel-shaped call sites.

### Outcome

- **`validate_type` now survives serialization.** The class object itself can't be written to
  JSON/HDF, so the *name* is stored in `validate_type_name` (added to `__state_fields__`) and
  resolved back through stardust's `SERIALIZABLE_CLASS_REGISTRY` by a lazy, caching property.
  Class-level defaults (`_validate_type = None`, `validate_type_name = ""`) cover the fact that
  stardust rebuilds via `cls.__new__(cls)` and never calls `__init__` — which was the actual
  mechanism of the bug. Verified: a restored list accepts valid writes and still rejects wrong
  types, and `add_measurement()` works after `restore_state()`.
- **`summarize()`** falls back to a plain repr for values that aren't `InstrumentState`, instead
  of calling `.state_str()` unconditionally. It also now iterates `populated_items()` rather than
  re-deriving indices.
- **`append(allow_expand=True)`** is implemented: a full list grows by one slot. The type check
  runs *before* `num_indices` is mutated, so a rejected value can't leave the list permanently
  one slot larger and empty.
- **Duplicate pairs collapsed.** `__getitem__`/`__setitem__` are the single implementation;
  `get_idx_val`/`set_idx_val` are one-line delegates. A test asserts they agree on populated,
  unpopulated, out-of-range and wrong-type cases.
- **Docstrings corrected** — `get_valid_idx`/`idx_is_populated`/`set_idx_val`/`get_idx_val` said
  "zero-indexed", contradicting the entire point of `first_index` (usually 1, to match instrument
  channel numbering).
- **`ChannelList` added** as an alias — deliberately `ChannelList = IndexedList`, not a subclass,
  since a subclass would register a second name in stardust's registry and break deserialization
  of anything already stored as an `"IndexedList"`.

Tests: **160 passed, 4 xfailed** (from 150/4).

### Note — what `validate_type` actually protects (surveyed 2026-08-24)

Asked during review: how automatic, and how circumventable, is the type checking? Measured
rather than assumed. It guards exactly one operation — *assigning a value into a slot of the
list*. It is a container-level guard ("what kind of object lives in this list"), not a schema
validator, and says nothing about the contents of those objects.

Enforced:

- `lst[2] = value`
- `lst.set_idx_val(2, value)` (a one-line delegate to `__setitem__`)
- `lst.append(value)` and `append(value, allow_expand=True)`
- `InstrumentState.set(params, value, indices=...)` **when the list slot itself is the final
  target** — but `set()` catches the `TypeError`, logs `Cannot set state. Rejected value for
  ...`, and returns `False`. A soft failure with a log line, not a raised exception. Callers that
  ignore the return value see nothing.

Not enforced:

- `InstrumentState.set(("channels", "div_volt"), v, indices=(2, None))` — walking *through* the
  list to an attribute on the element. Terminates in a plain `setattr()` and never touches
  `__setitem__`. **This is the shape of nearly every real state write in the codebase**, which is
  why the guard sees almost no traffic.
- direct pokes at `lst.index_data["idx-2"]`
- mutating an element in place (`lst[1].some_field = <anything>`)
- deserialization — stardust restores `index_data` wholesale without re-checking.

Instrumenting construction plus a full `refresh_state()`/`apply_state()` cycle across three
drivers gave 13 calls to `_check_type` and **0 rejections**; 12 of the 13 were a category
`__init__` pre-filling channel slots with objects it had just constructed itself. Corroborating
evidence that it was catching nothing: it was silently absent after *every* `restore_state()`
until the P6 fix above, and nobody noticed.

Verdict: keep it — it costs nothing and, now that `validate_type_name` serializes, a stored state
file documents what each list is meant to hold. But do not rely on it as a safety net. The gap
that matters (the `setattr` path) needs a different mechanism — see **P16**.

## Priority 7 — `add_param` / `__state_fields__` / `validate()` — **DONE**

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
- [x] **Implement the `__init_subclass__` auto-validate hook** in `InstrumentState`.
- [x] **Remove the now-redundant manual `self.validate()` calls** — 12 of them, across all six
      category modules. No state class can forget it now.
- [x] **`validate()` output fixed in the same pass.** No longer `print()`s; reports through
      `self.log` only, and returns a `bool` so callers can branch. Each warning carries a
      `detail` saying what the drift actually costs.
- [x] **`OscilloscopeMeasurementSetting` drift fixed** — `last_measured_value` added to
      `__state_fields__`. It was registered with `add_param()` but absent from the manifest, so a
      saved measurement came back without its value. The hook flagged it on the first run.
- [x] `OscilloscopeMeasurementMixinState` never called `validate()` — now automatic, and it
      validates clean.
- [x] `BasicVectorNetworkAnalyzerState` never called `validate()` — now automatic, and it
      validates clean.

### Found while implementing this

- [x] **`validate()` never reported a parameter missing from `__state_fields__`.** The second
      warning branch was guarded by `if len(missing_add_param) > 0:` — a copy-paste of the line
      above it, instead of `missing_state_field`. So a class whose *only* problem was the
      add_param-but-not-in-manifest case (exactly the drift that stops a field serializing, and
      exactly what `OscilloscopeMeasurementSetting` had) printed the stdout block but logged
      nothing. Two of the three bugs in this priority were the same bug wearing different hats.
      *confirmed*
- [x] `examples/validate_demo.py` was three lines that constructed a driver and relied on
      `validate()`'s stdout side effect to demonstrate anything. Rewritten to explain the
      mechanism and include a deliberately-drifting state class so the warning is visible.
- [ ] **Nothing consumes `validate()`'s return value yet.** It now returns `bool`, but the only
      caller is the auto-hook, which ignores it. If validation should ever be *fatal* (a
      `strict=True` mode for CI, say) that's the hook to put it in. No evidence it's needed —
      logged so the return value isn't mistaken for dead code.

## Priority 8 — networking (labmesh)

### pyfrost removal: source is clean, docs are stale

Verified: **no file under `src/` imports `pyfrost`, `GenCommand`, `Packable`, `NetworkCommand`,
`net_client`, or `net_server`.** `pyproject.toml` has `labmesh` + `pyzmq` and no
`pyfrost-network`. The migration in `docs/labmesh_migration_plan.md` is genuinely complete.
What remains is documentation and detritus:

- [x] **`CLAUDE.md` "Networking" section still describes the pyfrost design** and names three
      files that no longer exist (`network.py`, `net_client.py`, `net_server.py`). Actively
      harmful — it's the file that seeds agent context every session. Rewrite to describe
      `relay.py`'s `RemoteTextCommandRelayClient`/`Listener` and
      `networking/labmesh_net.py`'s `DriverStateBroadcaster`.
- [x] **`README.md`** — stale line `- Mention PyFrost (WIP)`.
- [x] **`oscilloscope_ctg.py`** — commented-out
      `# from constellation.networking.net_client import NetworkCommand, NetworkReply`.
- [x] **Stale bytecode**: `src/constellation/networking/__pycache__/` still contains
      `net_client.cpython-314.pyc`, `net_server.cpython-314.pyc`, `network.cpython-314.pyc` for
      deleted modules. Delete; consider a `.gitignore` entry.

### Networking bugs

- [x] **`query_binary` does not cross the network — waveform capture over labmesh silently
      returns empty data.** `RemoteTextCommandRelayClient` doesn't override `query_binary`, so the
      base raises `NotImplementedError`; `Driver.query_binary` catches it, logs, and returns `[]`.
      `RigolDS1000Z.get_waveform(binary=True)` is the *default* path and none of the networking
      examples pass `binary=False`. Biggest functional gap in the networking layer.
      Fix: base64 the block in `RemoteTextCommandRelayListener.query_binary`, decode client-side
      (the listener already returns lists-not-tuples for JSON's sake, so the convention exists).
      *static*
- [x] **`RemoteTextCommandRelayClient.close()` leaked the event-loop thread.** Now stops the
      loop, joins the thread with a bounded 2 s timeout (a hung loop must not wedge `close()`),
      and clears both handles so `_ensure_loop()` builds a fresh loop on reconnect rather than
      handing out a stopped one.
- [x] **No reconnection path** — fixed with `ReconnectPolicy`. One policy object per Driver,
      passed as `reconnect_policy=`, so a scope on a flaky mesh link and a PSU on local USB can
      behave differently. Two independent mechanisms:
      **in-call retry** (`retry_enabled`, default ON; `num_retries=2`, `retry_pause_s=0.25`),
      which absorbs the sub-second blip so the driver never goes offline at all; and
      **reconnect-on-use** (`reconnect_on_use`, default OFF; `reconnect_cooldown_s=5.0`), where
      an offline driver attempts a full `connect()` on next use, at most once per cooldown — a
      circuit breaker whose cooldown is the half-open probe. A Driver built without a policy gets
      a *copy* of module-level `DEFAULT_RECONNECT_POLICY` (change its fields at startup to shift
      the default application-wide) — a copy, not the object, so tuning one instrument doesn't
      silently retune every other driver.

      **The underlying problem was a deadlock, not just a missing retry.** `write`/`read`/`query`
      all early-return when `self.online` is False, and `check_online()` — the only thing that
      sets it back to True — is called *exclusively* from inside those methods' `except` blocks,
      which sit after that guard. So the code that could restore `online` was unreachable once
      `online` was False. Nothing self-healed, at any timescale, however brief the hiccup.
      `reconnect_on_use` is the explicit way out; it defaults OFF, which means **an offline
      driver still has no automatic path back unless the option is enabled** — the default
      in-call retry is what keeps transient failures from marking it offline to begin with.

### Reconnection follow-ups

- [x] **Distinguish offline-errors from other errors** — done, via `RelayErrorKind` and
      `classify_relay_exception()` in `relay.py`.

      The structural obstacle: every relay method catches its own exceptions and returns a bare
      success flag, so by the time the Driver saw a failure the exception was already gone and
      there was nothing left to classify. Relays now record the *kind* of their last failure on
      themselves (`relay.last_error_kind`, set by `note_failure()` in each `except` block and
      cleared at the start of every operation so a stale classification can't outlive the failure
      that produced it), and `Driver._relay_attempt()` acts on that.

      | kind | retried? | marks offline? |
      |---|---|---|
      | `TRANSPORT` — socket dropped, connection lost, timeout, invalid session | yes | yes |
      | `INSTRUMENT` — link worked, exchange didn't (unparseable reply, malformed block) | yes | **no** |
      | `USAGE` — unsupported operation, bad arguments | **no** | **no** |
      | `UNKNOWN` — unclassified | yes | yes (conservative: matches pre-classification behaviour) |

      Two concrete behaviours this fixes: calling `query_binary` on a relay that has none (VICP)
      no longer burns three attempts on a `NotImplementedError` and then declares the instrument
      offline; and a reply that won't parse no longer strands a driver offline on a perfectly
      good connection — permanently, with `reconnect_on_use` off.

      Timeouts are deliberately classified TRANSPORT: from the driver's side "no answer came
      back" is indistinguishable from a dead link, and it's the case retrying most often rescues.

- [ ] **The remote client can't classify a bench-side failure.** When
      `RemoteTextCommandRelayListener` reports `ok=False`, no exception crosses the mesh, so
      `RemoteTextCommandRelayClient.last_error_kind` stays `NONE` and the driver falls back to
      `UNKNOWN`. The listener knows the real classification (its local relay recorded one) and
      could return it alongside the failure. This is the same wire that the two-online-states
      item below needs, so do them together.
- [ ] **Track two independent online states: Driver→instrument and client→relay.** With a
      networked driver, "offline" currently collapses two genuinely different failures. If a GUI
      on machine 1 drives an instrument on machine 2, the questions "is the GUI still talking to
      the relay process?" and "is the relay process still talking to the hardware?" have
      different answers, different recoveries, and different things to show the user. A single
      `self.online` bool cannot express "mesh is fine, the scope is unplugged" or "the scope is
      fine, the bench machine dropped off the network". Needs a second tracked status, a way for
      `RemoteTextCommandRelayListener` to report *its* local relay's health back over RPC, and
      the reconnect policy to act on the right one — reconnecting the mesh link is useless if the
      instrument is what's unplugged.
- [ ] **Retried writes are not guaranteed idempotent.** A retried write can reach the instrument
      twice if the failure happened after delivery but before acknowledgement. Nearly all SCPI
      setters are idempotent so this is normally harmless, but an *action* command (a trigger, a
      relay toggle, an output enable) is not, and `_relay_attempt()` doesn't distinguish them.
      Options: a per-call `retry=False` opt-out, or marking action methods. Noted in the
      `_relay_attempt` docstring.
- [ ] **A reconnected instrument is not the instrument you left.** A scope that power-cycled
      comes back at factory defaults; silently resuming a sweep against it produces data that
      looks fine and is wrong. Wants an `on_reconnect` companion policy — `NOTHING` /
      `REFRESH_STATE` (re-read hardware so the tracker is at least honest) / `APPLY_STATE` (push
      the tracked state back, restoring the setup). `APPLY_STATE` is what an unattended run
      wants, and is also the one that can drive an instrument, so it shouldn't be the default.
- [ ] **`DriverStateBroadcaster` publishes stale state when its driver is offline.** It polls
      every `state_interval` regardless; `refresh_state()` "succeeds", every getter returns `""`,
      and subscribers see stale values with no indication they're stale. Should publish the
      online status alongside the state, or skip publishing while offline.
- [ ] **Consider a background supervisor** that probes offline drivers and reconnects before the
      next user call, rather than making that call pay for it. Better for unattended bench nodes,
      but it introduces concurrent relay access — and `DriverStateBroadcaster` already drives
      `poll()` from its own thread — so it needs a relay lock that doesn't exist today. Hold
      until something demands it.
- [x] **`Driver.connect()` referenced an undefined `e`** — fixed. There is no exception to
      report at that point: the relay connected and `query_id()` then cleared `self.online`
      because the instrument didn't answer `*IDN?`, so the message now says that.
- [x] **`Driver.check_online()`'s AUTO branch didn't skip the query for non-SCPI instruments** —
      added the missing `return`. The `xfail(strict=True)` on
      `test_check_online_skips_query_for_non_scpi_instrument` XPASSed and was removed.

### P8 outcome (query_binary + pyfrost doc cleanup) — **DONE**

`query_binary` now crosses the mesh. `RemoteTextCommandRelayListener.query_binary` packs the
locally-decoded values little-endian with the caller's `datatype` and base64s them;
`RemoteTextCommandRelayClient.query_binary` reverses that. Explicit little-endian (not native byte
order) means the bench machine and the controlling machine need not share an architecture.

Why base64 rather than a JSON list of numbers, measured on a 250k-point waveform (the DS1000Z's
max single-chunk transfer):

| encoding | size |
|---|---|
| raw bytes | 244 KB |
| **base64 (chosen)** | **326 KB** |
| JSON int list | 1116 KB (3.4x larger) |

**Timeout mismatch found and fixed while implementing this.** `DirectSCPIRelay` deliberately allows
30 s because a full-memory `:WAV:DATA?` chunk was confirmed on real hardware to take 15-20+ s — but
`RemoteTextCommandRelayClient` used a flat 10 s for every RPC, so the client would have abandoned
the transfer while the instrument was still legitimately sending. Added a separate
`binary_timeout_s` (default 60 s) and a `timeout_s` override on `_run()`. Without this the feature
would have appeared to work on small reads and failed on exactly the ones it was built for.

A local relay that cannot do binary reads (`VICPDirectSCPIRelay`) surfaces as a clean
`[False, ""]` from the listener rather than an unhandled `NotImplementedError` inside the
`RelayAgent`.

Doc cleanup: `CLAUDE.md`'s Networking section rewritten around labmesh and the "smart client, dumb
relay" split; `README.md`'s PyFrost line replaced; the commented-out `net_client` import removed
from `oscilloscope_ctg.py`; stale `net_client`/`net_server`/`network` `.pyc` files deleted.

Also brought two *other* CLAUDE.md passages up to date, which this session's earlier work had
falsified: the `@superreturn` description (drivers now **return** their value and must never assign
`_super_hint`; it is a descriptor class) and the dummy-mode description (`modify_state()` is the
single dispatch point; `@enabledummy` is a narrow escape hatch). Added pointers to
`docs/dummy_mode.md`, `docs/superreturn.md` and `todo_list.md`, plus a note explaining that a
reported XPASS means a bug was fixed and the marker should be removed.

Remaining pyfrost hits are confined to `src/constellation_core.egg-info/` — gitignored build
metadata from an old build, which regenerates on the next `pip install -e .`. Left alone.

Tests: **135 passed, 4 xfailed** (from 124/4). New coverage: exact round trips for uint8/int16/
int32/float/empty payloads, a full 250k-point waveform, an explicit little-endian wire-format
assertion, clean failure when the local relay can't do binary, corrupt/truncated payload rejection,
and a check that the binary timeout exceeds the text timeout.

### Binary transfer — implemented, with a known ceiling

- [x] **`write_binary` implemented** across all layers (`CommandRelay` contract,
      `DirectSCPIRelay` via pyvisa `write_binary_values`, `VICPDirectSCPIRelay` building the IEEE
      488.2 `#<n><count>` header by hand since pyvicp has no equivalent,
      `RemoteTextCommandRelayClient`/`Listener` over base64 RPC, and `Driver.write_binary`).
      Motivating case: loading an arbitrary waveform into an AWG, where the same points as
      comma-separated ASCII are several times larger and much slower.
- [x] **Documented the two-channel split** in `docs/networking_data_paths.md`: RPC is strictly
      JSON (`labmesh.util.dumps` is `json.dumps`, so base64 is the only way to carry bytes),
      while the DataBank has a native chunked binary protocol with SHA-256. Control traffic
      belongs on RPC; captured datasets belong in the bank.
- [x] **Size guard added on `query_binary`/`write_binary`.** `RPC_BINARY_WARN_BYTES` (2 MB of
      *encoded* payload) with `warn_if_oversize_rpc_binary()`, called on both the client's read
      and write paths and on the listener side too, so the warning also lands in the bench
      machine's log where the data originates. Deliberately a warning, not an error: the limit is
      about which channel is *appropriate*, not about what physically works, and a driver author
      mid-experiment shouldn't be blocked by a guess at where "too big" starts. Verified it does
      not fire on the 250k-point waveform the feature was built for.
- [ ] **`VICPDirectSCPIRelay` still has no `query_binary`** (pyvicp provides no block parser, so
      reading would mean hand-parsing the `#<n><count>` header off the raw stream). `write_binary`
      is implemented for VICP; the read direction is not. LeCroy scopes therefore can't do binary
      waveform reads. *static*

### OPEN — ASCII responses cross the mesh uncompressed (two candidate fixes, neither implemented)

**Status: logged, not implemented. Leaning toward option 1.**

Transport encoding currently follows *instrument* encoding. If a driver queries an instrument in
ASCII, that text crosses labmesh as a JSON string; there is no way to query ASCII and transport it
as binary. This is the "dumb relay" design working as intended —
`RemoteTextCommandRelayListener.query()` is literally
`list(self.local_relay.query(cmd))`, and it has no idea whether the string it is forwarding is
`"RIGOL TECHNOLOGIES,DS1054Z"` or 250,000 comma-separated floats. Re-encoding requires knowing the
response is a numeric list.

Measured cost, 250k-point **noisy sine** waveform (a realistic capture, not a repeating ramp —
an earlier measurement using repetitive data showed gzip at 133x and was meaningless):

| | size | vs today |
|---|---|---|
| **raw ASCII in a JSON string (today)** | 3296 KB | 1.0x |
| re-packed float32 + base64 | 1302 KB | 2.5x |
| gzipped ASCII | 1112 KB | 3.0x |
| gzipped float32 | 894 KB | 3.7x |

**Context that outweighs both options:** querying the same waveform via `query_binary` is **326 KB**
— 10x smaller than the ASCII path. Where the instrument supports binary blocks, using
`query_binary` beats every row above, and neither option below should be the first thing reached
for.

**Option 1 (leaning toward this) — add `query_ascii_values` as a relay primitive.**
pyvisa already provides `query_ascii_values(cmd, converter='f', separator=',')` (confirmed
present). The listener would parse ASCII into a numeric list, then pack + base64 it exactly as
`query_binary` does. Drivers would call `self.query_ascii_values(":WAV:DATA?")` instead of
`self.query(...)` followed by manual splitting.
- Stays honest about the dumb-relay principle: "parse a separated numeric list" is a standard SCPI
  concept pyvisa already models, not instrument-specific knowledge.
- 2.5x on the wire, *and* it removes hand-rolled parsing from drivers — `RigolDS1000Z.get_waveform`
  currently does `data[11:].split(",")` with a comment about a trailing-comma edge case that this
  would delete outright.
- Symmetric with the existing `query_binary`/`write_binary` pair, so it adds no new concepts.

**Option 2 — compress the RPC payload generically.** gzip large text responses in the
listener/client. Bigger win (3x) and fully transport-agnostic, benefiting every query rather than
just numeric ones.
- But this is really a **labmesh-level** concern. Doing it inside
  `RemoteTextCommandRelayListener` bakes a compression convention into Constellation that labmesh
  itself doesn't know about. It belongs in `labmesh.util.dumps`/`loads` so the whole mesh benefits
  (broker, databank announcements, state broadcasts), not only Constellation's relay.
- **Recommendation: raise this in the labmesh repo rather than working around it here.**

- [ ] Implement `query_ascii_values` across `CommandRelay` / `DirectSCPIRelay` /
      `RemoteTextCommandRelayClient` / `Listener` / `Driver` (option 1).
- [ ] Then migrate `RigolDS1000Z.get_waveform`'s ASCII branch onto it and delete the manual
      `split(",")` parsing.
- [ ] Separately: raise generic payload compression as an issue in the **labmesh** repo (option 2).
      Not a Constellation change.

### Found while fixing the P8 bug batch

- [x] **`query_id()` declared a silent instrument ONLINE.** It tested
      `if self.id.idn_model is not None:` — but `Driver.query()` returns `""` on *every* failure
      path (offline, relay error, empty reply) and never `None`, so the check could not fail. An
      instrument that answered nothing at all was marked online and merely flagged as failing
      hardware verification, and the driver then went on issuing commands to it. Found because
      the regression test written for the `connect()` `NameError` couldn't get `connect()` to
      return `False`. Now tests for actual content. *confirmed*

### Networking optimizations

- [ ] **Batch round-trips.** Every `Driver.query()` is a synchronous mesh round-trip via
      `asyncio.run_coroutine_threadsafe(...).result()`. `refresh_state()` on a 4-channel scope is
      ~15 sequential round-trips. Add a `query_many(cmds: list) -> list` RPC to the listener and
      use it from category `refresh_state()` where the driver supports it.
- [ ] **Deduplicate the example sets.** `examples/osc_networking/` and
      `examples/rigol_ds1000z_network/` are near-identical five-file sets
      (broker / bank / relay node / client / observer). Keeping both in sync is a tax — delete one.

## Priority 9 — wrong-name calls and signature mismatches — **DONE**

All of these are methods calling names that do not exist, or passing wrong argument shapes.

- [x] **`PowerSupply.set_current` and `set_output_enable` pass the wrong readback query** —
      both use `lambda: self.get_voltage(channel)`. On real hardware, setting current queries
      *voltage* back: `current_set`/`enable` never update and `voltage_set` is rewritten instead.
      *static*
- [x] **`PowerSupply.apply_state` calls `self.set_enable_output(...)`** — no such method
      (it's `set_output_enable`). The surrounding `try/except` swallows it as a `lowdebug`, so
      **apply_state silently fails to restore output enable on every channel, with no error.**
      *static*
- [x] **`PowerSupply.refresh_data` calls `self.get_output_measurement(ch)`** — no such method
      (it's `get_measured_output`). Uncaught `AttributeError`.
      *static*
- [x] **`PowerSupply.dummy_responder` has `case "set_enable"`** — no method produces that name.
      *static*
- [x] **`SpectrumAnalyzer.refresh_state`/`apply_state` call `get_num_points()`/`set_num_points()`**
      which are commented out in the same file -> `AttributeError`.
      *static*
- [x] **`SpectrumAnalyzer.refresh_state`/`refresh_data` call `self.get_trace_data(self, t_idx)`** —
      passing `self` as the first positional argument.
      *static*
- [x] **`Oscilloscope.apply_state` can't restore trigger source.** Calls
      `set_trigger_source(self.state.trigger_source)`, but the signature is
      `(channel:int=None, external:bool=False, line:bool=False)` and the stored value is a string
      like `"CHAN1"` -> `_format_trigger_source` does `"CHAN1" < 1` -> `TypeError`. Round-tripping
      a saved scope state fails here.
      *static*
- [x] **`get_all_waveforms` breaks the DS1000E.** It passes `_skip_run_management=True`, but
      `RigolDS1000E.get_waveform(self, channel)` takes no kwargs. `superreturn`'s `except` catches
      the `TypeError`, so `get_all_waveforms()` returns a list of `None`s and logs an error rather
      than raising.
      *static*

### Outcome

Before this pass, on a dummy driver: `SpectrumAnalyzer.refresh_state()`, `refresh_data()` and
`apply_state()` **all three** raised `AttributeError`, `PowerSupply.refresh_data()` raised
`AttributeError`, and `Oscilloscope.apply_state()` raised `TypeError` on the trigger source. All
now complete with zero errors logged.

Fixes applied:
- `PowerSupply.set_current` / `set_output_enable` now read back `get_current` / `get_output_enable`
  instead of `get_voltage`. Verified against a canned relay: `set_current` queries `:SOUR2:CURR?`
  and no longer `:SOUR2:VOLT?`.
- `PowerSupply.apply_state` calls `set_output_enable` (was `set_enable_output`), and its blanket
  `try/except ... lowdebug` — which is *why* that typo stayed invisible — was narrowed: it now
  skips unpopulated channels by an explicit `None` check and logs genuine failures via
  `self.error()`.
- `PowerSupply.refresh_data` calls `get_measured_output` (was `get_output_measurement`); the same
  wrong name in a log message was corrected.
- `SpectrumAnalyzer` gained a real category `get_trace_data`. The Siglent driver already
  implemented one under `@superreturn`, but the category's was commented out, so the super lookup
  raised `'super' object has no attribute 'get_trace_data'` on every call.
- `SpectrumAnalyzer.refresh_state`/`refresh_data` call `get_trace_data(t_idx)`, not
  `get_trace_data(self, t_idx)`.
- `num_points` calls dropped from `SpectrumAnalyzer.refresh_state`/`apply_state`, with a comment
  saying why — the accessors are commented out in the same file and the param isn't in
  `__state_fields__`.
- `Oscilloscope` gained `_parse_trigger_source()`, the inverse of `_format_trigger_source()`, so
  `apply_state()` can turn the stored `"CHAN2"`/`"EXT"`/`"AC"` back into the keyword arguments
  `set_trigger_source()` actually takes. All three forms now round-trip.
- `get_all_waveforms()` only sends `_skip_run_management=True` to drivers whose `get_waveform()`
  can accept it, detected by unwrapping the `superreturn` descriptor and inspecting the signature
  for the named param or `**kwargs`.

### Found while fixing P9

- [x] **`SiglentSSA3000X.get_freq_end` took a `points:int` argument** the category's
      `get_freq_end(self)` never passes. Since `superreturn` forwards args verbatim, every call
      raised `TypeError`, which `superreturn` swallowed — so `get_freq_end()` silently returned
      `None`. Signature corrected. *confirmed*

Tests: **124 passed, 4 xfailed** (from 112/4). New coverage includes the readback-queries-the-
right-parameter check against a canned relay, the parametrized trigger-source round trip,
`_parse_trigger_source` as a proven inverse of `_format_trigger_source` plus its junk handling,
and a `get_all_waveforms()` test using a driver whose `get_waveform()` takes no kwargs. All six
dummy examples still run clean.

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

### OPEN QUESTION — the "instrument data vs. instrument settings" split (needs a decision)

**Status: unresolved. Do not clean up until the design question below is answered** — the three
vestiges here are all fragments of one abandoned design, and deleting them piecemeal would throw
away the classification work that a real answer needs.

Three separate mechanisms exist to express "this parameter is measurement *data*, not a *setting*".
None of them is wired to anything:

1. **`DataEntry` + `Driver.data` — completely dead.** `DataEntry` (`base.py`, holds
   `update_time` / `value` / `data_hash`) is **never instantiated anywhere in the repo**. The only
   references are the class definition itself, the `self.data = {}` line in `Driver.__init__`
   whose comment claims "Each value is a DataEntry instance", and two mentions in prose docs.
   Verified at runtime: `driver.data` is `{}` after construction and still `{}` after
   `refresh_data()` — every category's `refresh_data()` writes into `self.state`, never into
   `self.data`. The real data lives in e.g. `state.channels[1].waveform`. `data_hash` carries its
   own TODO arguing against itself ("complicated and I'm not sure it's really worth while").
   *confirmed*
2. **`InstrumentState.is_data` + `add_param(is_data=True)` — recorded but never consulted.**
   Five params are flagged across the categories (`Oscilloscope.waveform`, `SpectrumAnalyzer.
   waveform`, `VNA.data`, `DAQ.last_value_V`, `DAQ.last_acquisition`). The flag has no effect:
   `waveform` is marked `is_data=True` and still appears 8 times in `state_to_dict()` output.
   `base.py` already carries the comment `# is_data is not used.`
   *confirmed*
3. **`state_to_dict(include_data=...)` / `dump_state(include_data=...)` — dead parameter**, never
   referenced in either body (see the existing item below).

**The question to answer first:** should measurement data be persisted alongside instrument
settings at all? Waveforms and traces aren't small, and Constellation now has a labmesh `DataBank`
that exists precisely to hold bulk datasets. Plausible answers:
- **(a)** Data never goes in the state file. `include_data` is removed, `is_data` becomes the
  exclusion filter in `state_to_dict()`, `DataEntry`/`Driver.data` are deleted. Bulk data goes to
  the databank.
- **(b)** Data is opt-in via `include_data=True`, implemented using `is_data` as the filter.
  `DataEntry`/`Driver.data` still deleted (state fields already carry the data).
- **(c)** Revive `Driver.data` as a genuine separate store with timestamps — the original intent.
  Highest cost; needs a reason `is_data`-flagged state fields can't serve.

Current lean is (a) or (b): the data already lives in `state`, so `Driver.data` is redundant
regardless of which persistence rule is chosen — but that is a call to make deliberately, not by
default.

**Cleanup blocked on that decision:**
- [ ] Decide (a)/(b)/(c) above.
- [ ] Then: delete `DataEntry` and `Driver.data` (~15 lines) if the answer is (a) or (b).
- [ ] Then: audit `is_data` labelling for consistency — it is currently wrong in at least one
      place. `PowerSupplyChannelState.voltage_meas`/`current_meas` are measurements and are **not**
      flagged, while `DAQ.last_value_V` is. Whatever the rule turns out to be, the labels have to
      agree with it before they can drive behavior. *confirmed*
- [ ] Then: resolve `include_data` (item immediately below) as part of the same change.

---

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

## Priority 14 — feature gaps

### Add a `num_points` (sweep points) field to the SpectrumAnalyzer category

Not a bug — a missing feature. The scaffolding was written, then commented out everywhere, and
P9 removed the dangling calls that referenced it. Adding it properly means touching four places:

1. **`SpectrumAnalyzerState`** (`spectrum_analyzer_ctg.py`) — uncomment/restore
   `self.add_param("num_points", unit="1")` and add `"num_points"` to `__state_fields__`.
   Both are required; `validate()` warns if they disagree.
2. **Category accessors** — `set_num_points(self, points:int)` and `get_num_points(self)`,
   following the standard shape:
   ```python
   @abstractmethod
   def set_num_points(self, points:int):
       self.modify_state(self.get_num_points, ["num_points"], points)

   @abstractmethod
   def get_num_points(self):
       return self.modify_state(None, ["num_points"], self._super_hint)
   ```
3. **Driver implementation** (`Siglent_SSA3000X_dvr.py`) — replace the two commented stubs with
   real `@superreturn` methods that write/query the sweep-point count and `return` the parsed
   value (drivers no longer assign `_super_hint`; see `docs/superreturn.md`).
4. **Re-add the calls** to `SpectrumAnalyzer.refresh_state()` and `apply_state()`. Both currently
   carry a `# NOTE: num_points is deliberately absent` comment marking the spot — delete those
   comments when the feature lands.

**Pitfalls in the existing commented-out code — do not just uncomment it:**

- The commented getter is `get_num_points(self, channel:int=1)` while the commented setter is
  `set_num_points(self, points:int)` — **mismatched signatures**. A spectrum analyzer has traces,
  not channels, so neither should take `channel`. This is the same shape as the
  `SiglentSSA3000X.get_freq_end(points:int)` bug fixed in P9, where `superreturn` forwards args
  verbatim, the driver raises `TypeError`, and the decorator swallows it into a silent `None`.
- The commented getter carries `@enabledummy`. Under the post-P3 rules it must **not** —
  `num_points` maps to a plain state field, so `modify_state()` handles dummy mode generically.
  See `docs/dummy_mode.md`.
- The driver file has **two identical commented stubs both named `get_num_points`**; one was
  clearly meant to be `set_num_points`.
- Don't copy the VNA's version as a template — `BasicVectorNetworkAnalyzerCtg.set_num_points`
  is *per-channel* (`["channels", "num_points"]`, `indices=[channel]`). The SA's is a single
  instrument-wide value.

**Open question to resolve first:** the original author's comment on the `add_param` line reads
`# I think this doesnt actually exist`, i.e. there was doubt about whether the sweep-point count
is settable at all on this instrument. Confirm against the Siglent SSA3000X programming guide
before implementing (likely `[:SENSe]:SWEep:POINts`, **unverified**). If some spectrum analyzers
expose it and others don't, that is what `FeatureUnavailable` (already defined in `base.py`) is
for — raise it in drivers that can't support it rather than omitting the category method.

- [ ] Confirm the SCPI command and whether sweep points is settable on the SSA3000X.
- [ ] Add `num_points` to `SpectrumAnalyzerState` (`add_param` + `__state_fields__`).
- [ ] Add category `set_num_points`/`get_num_points` with matching signatures, no `@enabledummy`.
- [ ] Implement both in `Siglent_SSA3000X_dvr.py` (return the value, don't assign `_super_hint`).
- [ ] Re-add the calls in `refresh_state()`/`apply_state()` and delete the two placeholder NOTEs.
- [ ] Seed it in `SpectrumAnalyzer.init_dummy_state()` (currently empty — see the P3 follow-ups).

## Priority 15 — planned: split niche-dependency drivers into separate repos

**Owner's stated direction (2026-08-20), not yet scheduled.** `constellation-core` should carry
only `pyvisa`- and `pyserial`-based drivers. Anything needing a niche or proprietary package
(`pyvicp`, `PyDAQmx`/`nidaqmx`, `zhinst`, ...) moves to its own repo, so users don't install a pile
of vendor bloat they'll never load.

Recording this because it changes what "stable" means for the relay layer — don't invest in
polishing `VICPDirectSCPIRelay` in-place, and don't add new hard dependencies to `pyproject.toml`
without checking against this plan.

### Current footprint (surveyed 2026-08-20)

`pyproject.toml` declares 13 hard dependencies. The niche ones and who actually imports them:

| package | imported by | note |
|---|---|---|
| `pyvicp` | `relay.py` only | `from pyvicp import Client` at module top — **unconditional** |
| `zhinst >= 24.0.0` | `to_reformat/ZurichInstruments_MFLI_dvr.py` only | **already dead weight** |
| `PyDAQmx` / `nidaqmx` | nothing | not a dependency; `data_acquisition/drivers/` is empty |
| `pyserial` | nothing | not a dependency yet, and no serial driver exists |
| `PyQt6` | `ui.py`, `widgets.py`, 3 `*_gui.py` | heavy; same optional-extra question |
| `matplotlib` | `base.py:15`, `ui.py`, `oscilloscope_ctg.py`, ... | **ACCEPTED as a hard dependency** — not in scope for the split (see below) |

Two findings worth acting on ahead of the full split:

- [x] **`zhinst` dropped from `pyproject.toml`** (2026-08-20). A note was left at the import
      site in the unmigrated MFLI driver explaining why, and that it belongs in its own repo.
      Original finding: Its only importer is an unmigrated
      driver in `to_reformat/` that isn't exported from `all.py`, so nothing reachable uses it.
      Every user currently installs it for nothing. This is a one-line change independent of the
      repo split. *confirmed*
- [ ] **`pyvicp` can't become optional while `relay.py` imports it at module scope.** Either move
      `VICPDirectSCPIRelay` out to the new repo (the stated plan) or make the import lazy/guarded
      so `relay.py` still imports on a machine without `pyvicp`. Worth doing the lazy-import
      version first if the repo split is far off — it decouples the two changes. *confirmed*

### Design questions to settle before splitting

- [ ] **Mechanism**: `[project.optional-dependencies]` extras in one repo
      (`pip install constellation-core[vicp]`) vs genuinely separate distributions
      (`constellation-vicp`). Extras are far less work and keep drivers discoverable; separate
      repos give independent release cadence and keep vendor licensing out of the core tree.
      The stated plan is separate repos — worth confirming that's still preferred over extras.
- [ ] **What the satellite repos depend on**: they need `Driver`, `CommandRelay`,
      `InstrumentState` from core, so core's public API becomes a real compatibility surface
      rather than something freely refactorable. Several open items in this file
      (`_super_hint`/`superreturn`, `IndexedList` cleanup) are easier *before* that hardens.
- [ ] **Driver discovery**: `instrument_control/all.py` currently `import *`s every driver.
      Out-of-tree drivers need a registration path — entry points, or explicit user imports.
- [-] ~~Should `matplotlib` become optional?~~ **Decided 2026-08-20: no.** It's ubiquitous enough
      that requiring it is not a burden, unlike vendor-specific packages. Keep it a hard
      dependency; don't re-raise this. The split is about *niche/proprietary* packages, not about
      minimising the dependency count generally.
- [ ] **Should `PyQt6` become optional?** Still open — unlike `matplotlib` it is genuinely heavy
      and only used by `ui.py`/`widgets.py`/the `*_gui.py` modules, none of which a headless or
      script-only user touches. Distinct from the driver split, but the same mechanism would
      serve it.

### Related cleanup

- [ ] **`examples/serial_demo.py` is misnamed and broken.** Despite the name it has nothing to do
      with serial ports — it's a *serialization* demo, and it imports `stateclass`, `serializer`
      and `base` as top-level modules, none of which exist. Rename or delete; it's misleading
      when scanning for existing serial support. *confirmed*

## Priority 16 — far-future: extend `add_param()` into real type checking

Low priority, no urgency, listed here so the analysis behind it isn't lost. Do not start this
before the higher-priority items are cleared.

**The gap.** `IndexedList.validate_type` only fires on writes into a list *slot*, and essentially
every real state write in the codebase instead walks *through* a list to an attribute on the
element and lands in a plain `setattr()` (see the note under P6 for the measured breakdown). So
today nothing stops `state.set(("channels", "div_volt"), "0.5 volts")` from storing a string
where a float belongs. That value then propagates into `apply_state()`, into serialized state
files, and into whatever plot or SCPI command consumes it — the failure surfaces far from the
write that caused it.

**Why `add_param` is the right lever.** It is already the per-instance registry that every state
parameter passes through, and it already carries `unit` and `is_data` metadata. Adding an
expected type (and possibly a range) there keeps one declaration site per parameter rather than
introducing a third parallel list. `InstrumentState.set()` would consult it before the
`setattr()`, closing the path `validate_type` structurally cannot reach.

- [ ] **Decide whether this augments or replaces `validate_type`.** If `add_param` gains type
      checking, the IndexedList guard becomes redundant for anything registered as a param —
      though it would still cover raw `lst[i] = v` writes and `append()`, which `add_param` never
      sees. Leaning augment (keep both, different scopes), but that is not settled.
- [ ] **Decide the failure mode: raise or log-and-return-False?** `InstrumentState.set()`
      currently swallows the `TypeError` and returns `False`, which is consistent with the rest of
      that method's error handling but easy to ignore. A hard raise would catch driver bugs
      immediately, at the cost of a driver returning a slightly-off type from a getter being able
      to take down a `refresh_state()` mid-sweep. Possibly a per-Driver strictness flag.
- [ ] **Decide how strict is useful.** `int` where a `float` is expected, numpy scalars, and
      `None` for "not yet read" all have to be acceptable, or the check will be turned off within
      a week. Probably `numbers.Real` style abstract types rather than concrete ones.
- [ ] **Check the interaction with `validate()`** (P7) — that method already cross-checks
      `add_param` against `__state_fields__`; a type declaration is naturally checked in the same
      place.
- [ ] Retrofit the existing `add_param` call sites across all category classes once the shape is
      settled. Large mechanical change; worth doing in one pass, not incrementally.

## Priority 17 — GUI layer

Numbered last only because it was added last; these are ordinary near-term items, not deferred
like P16. The architecture itself is settled and built — see `docs/gui_architecture_proposal.md`,
`docs/gui_authoring_guide.md`, and commit `a2c552c`. `InstrumentBridge`/`OwningBridge`/
`ObserverBridge`, `TrackedControl`/`IndicatorButton`, `register_gui()` and the `QDockWidget`
container all exist in `ui.py`, with widgets registered for `Oscilloscope`, `PowerSupply` and
`DataAcquisition`.

- [ ] **No test coverage for `ui.py` at all.** The suite is 200 tests of state/dummy/networking
      and zero touching the GUI layer. Both of the pieces most worth testing are testable
      headless — Qt's `offscreen` platform plugin needs no display:
      - the **bridge threading contract**: `request()` returns immediately; a slow driver call
        stalls only its own bridge's queue and not other bridges; `command_result` fires with
        `success=False` and the exception when a driver method raises; `stop()` actually ends the
        worker thread.
      - the **`TrackedControl` status state machine** (confirmed / pending / mismatch / stale).
        This is pure logic over signal inputs, needs no rendering, and is exactly the kind of
        thing that rots silently — a wrong indicator doesn't crash, it just quietly lies about
        whether the instrument did what you asked.
      Neither needs a real instrument; a dummy-mode driver plus a fake bridge is enough.
- [ ] **Protect the data-race guard in `OwningBridge._poll_and_emit()`.** It deliberately
      reconstructs a fresh `InstrumentState` with `from_serial_dict(state_dict)` rather than
      emitting `self.driver.state` directly. That is *not* a redundant allocation: Qt signals
      pass Python object references across threads, and the worker thread keeps mutating
      `self.driver.state` in place on every subsequent poll — emitting it would hand the GUI
      thread an object that changes underneath it. It looks exactly like something worth
      "optimizing away", and the resulting corruption would be intermittent and awful to
      diagnose. There is a comment explaining it; there is no test that would fail if someone
      removed it. Add one (assert the emitted object is not `bridge.driver.state`, and that a
      subsequent poll doesn't mutate the previously emitted object).
- [ ] Categories still without a registered widget: vector network analyzer, digital multimeter,
      spectrum analyzer, arbitrary waveform generator. Per-category work now, not framework work.
- [ ] `widgets.py` (54 lines, `StatusPushButton`) is the pre-`a2c552c` prototype and predates the
      current architecture. Fold anything still wanted into `ui.py` and delete it, or say in the
      file what it's still for.
- [ ] **`TrackedControl` can't distinguish "the mesh dropped" from "the instrument is unplugged"**
      — the same single-`online`-bool limitation logged under the P8 reconnection follow-ups.
      Those two failures want different indicators and different user guidance, so whatever
      two-level online tracking gets built there needs a matching indicator state here.

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
