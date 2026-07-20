# Dummy-mode and state tracking/updating/saving system: review

Inspected `src/constellation/base.py` (`Driver`, `InstrumentState`, `IndexedList`, `modify_state`,
`enabledummy`/`superreturn`, `state_to_dict`/`dump_state`/`restore_state`/`load_state_dict`) and
`oscilloscope_ctg.py`/`Rigol_DS1000Z_dvr.py` as the concrete category+driver pair exercising all of
it. Tests are in `tests/test_dummy_state.py` (14 passing, 10 `xfail(strict=True)` — each `xfail`
asserts the *correct* behavior for a confirmed bug below, not the current one; `strict=True` means
the test will flip to a hard failure the moment someone fixes the bug without updating the test,
which is the intended "please remove this marker now" signal).

## How dummy mode actually dispatches (for context on the bugs below)

A driver method like `RigolDS1000Z.set_div_volt` is decorated `@superreturn`. In dummy mode this
skips the driver's own SCPI body and calls the *category* class's `set_div_volt`, which calls
`self.modify_state(lambda: self.get_div_volt(channel), [...], value)`. `modify_state` itself checks
`self.dummy` and, if true, stores `value` directly without ever calling the query lambda — this
path works correctly and needs no per-method dummy support at all.

Separately, some (not all) category-level methods are *also* decorated with `@enabledummy`, which
intercepts the call before it ever reaches `modify_state` and redirects to
`self.dummy_responder(func_name, *args)` instead. `Oscilloscope.dummy_responder` is a hand-written
`match func_name:` table mirroring a subset of getters back from `self.state`. Two independent
"dummy mode" mechanisms exist side by side, applied inconsistently — that inconsistency is the
direct cause of three of the bugs below.

## Confirmed bugs

1. **`set_trigger_mode`/`set_trigger_level`/`set_probe_attenuation`/`set_bandwidth_limit`/
   `set_trigger_source` silently do nothing in dummy mode.** These are decorated `@enabledummy` at
   the category level (unlike `set_div_volt`/`set_offset_volt`/`set_chan_enable`/`set_coupling`/
   `set_div_time`/`set_offset_time`, which aren't), so in dummy mode they route to
   `dummy_responder()` instead of `modify_state()`. `Oscilloscope.dummy_responder` has no `case` for
   any of these `set_*` names, so they hit the generic fallback (`return -1`) and never touch
   `self.state` at all. Confirmed: `osc.set_trigger_mode(...)` followed by
   `osc.state.trigger_mode` reads back `None`, silently. **Highest-impact bug found** — anyone
   building a dummy-mode demo/test around triggering or probe settings gets silently wrong
   behavior with no error. Tests: `test_dummy_set_trigger_mode_persists_to_state` (+ level, +
   attenuation).

2. **`InstrumentState.set(..., fragment=X)` raises `UnboundLocalError` for an unrecognized
   fragment**, instead of logging and returning `False` like every other invalid-input path in the
   same function. The error message references the local variable `obj_top` before it's ever
   assigned (that assignment only happens later, in the non-fragment code path). Confirmed via
   direct repro. Test: `test_instrumentstate_set_bad_fragment_fails_gracefully`.

3. **`InstrumentState.get()` has no `fragment` parameter at all**, unlike `set()`. There is
   currently no way to read back a value written into a `state_fragment` through the top-level
   `state.get(...)` API — you'd have to reach into `state.state_fragments[name].get(...)` directly.
   Test: `test_instrumentstate_get_supports_fragment_like_set_does`.

4. **Mutable default argument: `RigolDS1000Z.__init__`, `Rigol_DS1000E.__init__`, and
   `Siglent_SSA3000X.__init__` declare `relay:CommandRelay=DirectSCPIRelay()`** as a default
   parameter value, which Python evaluates once at function-definition time. Every instance built
   without an explicit `relay=` kwarg shares the *same* `DirectSCPIRelay` object. Confirmed
   directly: constructing a second `RigolDS1000Z` silently overwrites the first one's
   `relay.address` (`osc1.relay is osc2.relay` → `True`; `osc1.relay.address` changes to `osc2`'s
   address after `osc2` is built). This is a real correctness bug for any real (non-dummy) two-scope
   script that doesn't pass `relay=` explicitly — writes could go to the wrong instrument. Other
   drivers (Keithley, Keysight, Siglent SDM/SDG, Rigol DP832, R&S ZVA) don't have this bug — they
   construct `DirectSCPIRelay()` fresh inside their `__init__` body instead of as a signature
   default. Test: `test_relay_default_argument_is_not_shared_between_instances`.

5. **`Driver.check_online()`'s `CheckOnline.AUTO` branch doesn't actually skip the query for
   non-SCPI instruments.** It warns "Cannot use CheckOnline.AUTO for non-SCPI instruments.
   Defaulting to OFFLINE." and sets `self.online = False`, but there's no `return`/`elif` after
   that — execution falls through to an unconditional `self.relay.query("*IDN?")` a few lines
   later, immediately overwriting `self.online` based on whatever that query returns. Confirmed
   with a fake relay that returns a non-empty string for any query: `self.online` ends up `True`
   despite `is_scpi=False`. Test: `test_check_online_skips_query_for_non_scpi_instrument`.

6. **`Driver.state_to_dict(include_data=False)`'s `include_data` parameter is dead** — never
   referenced in the function body. `self.data` (the `DataEntry` values — actual measurement
   results, as opposed to instrument settings) is never included in the saved dict regardless of
   the flag, contradicting the docstring ("Optional argument to include instrument data state as
   well"). Test: `test_state_to_dict_include_data_flag_has_effect`.

7. **`OscilloscopeState.channel_colors` (a plain dict keyed by integer channel numbers) silently
   loses all its data when saved via `dump_state()`.** `jarnsaxa.dict_to_hdf`'s `write_level()`
   turns every dict into an HDF group; `h5py.Group.create_group()`/`create_dataset()` raise
   `TypeError` for non-string names. Confirmed directly (`h5py.create_group(1)` →
   `TypeError: A name should be string or bytes, not <class 'int'>`). The failure never surfaces to
   the caller because of two compounding issues in `jarnsaxa.dict_to_hdf` itself: `write_level()`
   never checks/propagates the return value of its own recursive calls for nested dicts, and the
   top-level function's explicit "failure" branch still does `return True`. Net effect: `dump_state()`
   reports `True` while `channel_colors`'s HDF group is silently written completely empty (confirmed
   by inspecting the file directly). This is the exact class of problem `IndexedList` was built to
   avoid (it stringifies its integer indices to `"idx-N"` for exactly this reason) — that
   convention just wasn't applied to `channel_colors`. Test: `test_dump_state_preserves_channel_colors`.
   *(The two `dict_to_hdf`/`write_level` issues live in the `jarnsaxa` sibling repo, not here — see
   architectural notes below.)*

8. **Values round-tripped through `dump_state()`/`restore_state()` come back as numpy scalar types,
   not the original Python types.** A Python `bool` becomes `numpy.bool_`, `float` becomes
   `numpy.float64`, `int` becomes `numpy.int64` (confirmed for `chan_en`, `div_time`, `div_volt`,
   `num_channels`). Values still compare equal with `==`, but `isinstance(x, bool)`/`type(x) is
   float` checks, or re-serializing the restored state to JSON elsewhere, would behave differently
   after a restore than before one. Test: `test_dump_restore_preserves_native_python_types`.

### Minor / lower-priority

- `Driver.__init__` sets `self.dummy = False` and then, a dozen lines later, `self.dummy = dummy` —
  a harmless but confusing dead assignment (nothing reads `self.dummy` in between). Simplify-only,
  no behavior change, no test written for it.
- `Oscilloscope.dummy_responder`'s fallback convention for *unrecognized* function names is
  inverted relative to the base `Driver.dummy_responder`'s fallback: the base class returns `None`
  for unrecognized `set_*` and `-1` for unrecognized `get_*`; the oscilloscope override does the
  opposite (`-1` for `set_*`, `None` for `get_*`). Confusing, but the real damage from hitting this
  path is bug #1 above (state not being written at all), not the specific sentinel value returned.
- Dummy-mode waveforms (`remake_dummy_waves`) omit the `"channel"` key that real (non-dummy)
  `get_waveform()` includes, and mix `numpy.ndarray`/`numpy.float64` values into what's otherwise a
  plain dict — inconsistent shape between dummy and real waveform dicts. Not verified to break
  anything downstream (jarnsaxa's serializer handles numpy scalars/arrays generically), so treated
  as a low-priority consistency note rather than a full bug entry.

## Suggested architectural updates

1. **Collapse dummy-mode dispatch to one mechanism.** Having `@enabledummy`, `@superreturn`, and
   `modify_state()`'s own `self.dummy` check as three separate places dummy behavior can live is
   exactly how bug #1 happened — it's easy to decorate a new `set_*` inconsistently and get silent
   data loss with no error. Since `modify_state()` already handles the dummy case correctly and
   generically for anything that maps to a single state field, the cleanest fix is to **stop using
   `@enabledummy` on `set_*` category methods entirely** and reserve `dummy_responder()` for methods
   with no natural state-field mapping (pure actions like `run_acquisition`, `do_single_trigger`).

2. **Stop hand-maintaining `dummy_responder`'s getter table.** The oscilloscope's `match func_name:`
   table is missing 5+ getters (trigger mode/level/source, probe attenuation, bandwidth limit) with
   no test or type system catching the gap — new getters are silently unsupported in dummy mode
   until someone notices. Since the vast majority of `get_*` dummy fallbacks are just "read this
   same state path back," consider deriving that generically (e.g. `get_*`'s dummy behavior reads
   `self.state` via the same `params`/`indices` its `modify_state` call already uses) rather than
   requiring a hand-written mirror per method. Reserve the explicit `dummy_responder` table only for
   genuinely synthetic behavior like `get_waveform`'s generated signal.

3. **Give `InstrumentState.set()`/`get()` a shared path-resolution helper.** The fragment-support
   asymmetry (bug #3) and the `obj_top` bug (bug #2) both stem from `set()` and `get()` having grown
   independently with no shared logic for "resolve this params/indices/fragment path." A private
   `_resolve(params, indices, fragment)` used by both would make it structurally impossible for the
   two methods' supported features to drift apart again, and centralizes the class of bug in one
   place instead of two parallel implementations.

4. **Make state-persistence type fidelity a deliberate contract.** Right now a restore silently
   changes every scalar's Python type to its numpy equivalent. Either cast back to native Python
   types when reconstructing scalar leaves (`bool(x)`/`float(x)`/`int(x)`), or explicitly document
   this as an accepted, permanent limitation — right now it's neither, it's just a surprise waiting
   for whoever hits it next.

5. **Treat non-string-keyed dicts as disallowed in state fields.** `channel_colors` breaking HDF
   serialization is the identical problem `IndexedList` already solved (by stringifying its integer
   indices) — the fix just wasn't applied consistently. Since the failure mode is a *silent, fully
   swallowed* data loss rather than a visible error, I'd rather see this enforced structurally than
   left as a convention to remember: e.g. have `InstrumentState.add_param()` reject/warn on dict
   values whose keys aren't strings, so this can't recur the next time someone adds a per-channel
   dict-shaped setting.

6. **Decide what `include_data` on `state_to_dict()`/`dump_state()` is actually supposed to do, and
   implement it.** The dead parameter plus the existing `is_data`/`DataEntry` split in
   `InstrumentState` strongly suggests persisting measurement *data* alongside instrument *state*
   was planned but never finished. Worth a real design pass (what does "data" mean per category?
   how large can it get - waveforms aren't small - does it belong in the same HDF file at all,
   or should large datasets go through something more like the labmesh databank from the networking
   work?) rather than a one-line fix.

7. **`jarnsaxa.dict_to_hdf`'s success/failure contract is unreliable** (bug #7's root cause): nested
   `write_level()` calls' return values are discarded, and the top-level function returns `True`
   even on its own explicit failure branch. This lives in the `jarnsaxa` sibling repo, not here, so
   worth flagging there directly rather than working around it inside Constellation - but until
   it's fixed, `Driver.dump_state()`'s docstring promise ("Returns: bool: True if successfully saved
   file") is not actually reliable, and callers relying on it to detect a failed save are trusting a
   guarantee that doesn't hold.

## Test summary

`tests/test_dummy_state.py`: 14 passed (IndexedList reentrancy/bounds, `InstrumentState` scalar and
indexed set/get, dummy-mode connect and the setters that *do* work correctly, dummy waveform
clipping, and a basic dump/restore round trip), 10 `xfail(strict=True)` pinning down each bug above
with the desired correct behavior. Run with `pytest tests/ -v`.
