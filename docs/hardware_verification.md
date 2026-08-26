# Hardware verification

*How Constellation records which driver methods have actually been checked against a physical
instrument — and why that is a different question from what the instrument can do.*

## The problem

Nothing in a repository tells you whether `set_trigger_level()` was ever run against a real scope.
Git history answers *"was this diff reviewed"*; it does not answer *"does this SCPI string do what
we think on a DS1054Z at firmware 00.04.04"*.

That second fact has properties git can't carry:

- it is **per-method**, not per-commit — a change touching twelve methods may have been checked for
  three;
- it is **per-model** — a DS1052E and a DS1054Z share one driver, and a command can work on one and
  not the other;
- it is **perishable** — a firmware update can invalidate it;
- it is **re-checkable** — unlike a merge approval, you can run it again next month;
- and it is **user-facing** — the person picking up your VNA driver wants to know which methods are
  trustworthy.

So it is recorded as data in the repository, not as process around it.

## Two halves, deliberately separate

| | question | lives in | written by |
|---|---|---|---|
| **capability** | *Can this instrument do this at all?* | driver source, as decorators | a human, when writing the driver |
| **verification** | *Has the implementation been checked against hardware?* | `verification.yaml` | the hardware test run |

Capability belongs next to the method because it is a property of the instrument. Verification
belongs in a data file because it is an *event* — with a date, a model, a firmware and an operator
— and because a test run can then write it, which a human typing dates into source cannot be
trusted to keep accurate.

**Capability is never restated in YAML.** It is derived, and a cross-check test fails if a file
tries to duplicate it.

```mermaid
flowchart TD
    A["driver method"] --> B{"decorated?"}
    B -- "@feature_unavailable" --> C["UNAVAILABLE<br/>hardware cannot. permanent."]
    B -- "@feature_unimplemented" --> D["UNIMPLEMENTED<br/>not written yet. a work queue."]
    B -- "no" --> E["look up verification.yaml"]
    E --> F["UNVERIFIED · ROUNDTRIP · CONFIRMED · FAILED"]
    C --> G["capability_report()"]
    D --> G
    F --> G
```

## The three code-side states

There are genuinely three, and using two markers for them loses the information that matters most:

```python
@feature_unavailable("DS1000E cannot query the timebase over SCPI")
def get_div_time(self):
    pass                     # permanent. no driver work will change this.

@feature_unimplemented("SCPI is documented but not written or checked")
def get_trigger_level(self):
    pass                     # a to-do. someone should pick this up.

@superreturn
def get_div_volt(self, channel:int):
    return float(self.query(f":CHAN{channel}:SCAL?"))   # implemented -> verification applies
```

The two decorators behave identically at the call site — both raise `FeatureUnavailable`, both are
skipped during state sweeps — but they mean opposite things about the future.
`unavailable_features()` lists what will never work; `unimplemented_features()` lists what someone
should go and finish. `RigolDS1000E` is the worked example: four of the first, sixteen of the
second.

## The two flavours of "verified"

A round trip can be **self-consistently wrong**. If a driver's setter writes the timebase and its
getter also reads the timebase, then setting volts/div and reading it back agrees perfectly — and
the driver is broken. No machine can catch that; only a person looking at the instrument's front
panel can.

So there are two statuses, and they record different facts:

| status | meaning |
|---|---|
| `unverified` | implemented, never checked against hardware. |
| `roundtrip` | set/read-back agreed on real hardware. Proves the set/get pair is **self-consistent** — *not* that it controls the parameter it claims to. |
| `confirmed` | a person watched the instrument and confirmed the physical effect. |
| `failed` | checked and did not work. Kept deliberately: "we tried and it broke" is far more useful than "nothing is known". |

Note that for **action commands** — `run_acquisition`, `do_single_trigger`, `preset` — there is no
read-back at all. `roundtrip` is not merely weaker for those; it is unreachable. They can only ever
be `confirmed`.

`failed` is never outranked into silence by a weaker passing record. It is evidence of *not*
working, not weak evidence of working.

## The record format

`verification.yaml` sits **in the same directory as the drivers it describes** —
`src/constellation/instrument_control/<category>/drivers/verification.yaml`. It is package data, so
it ships with the install and, crucially, a driver that moves to its own repository (see the planned
split of VICP/DAQmx/zhinst drivers) carries its own records with no central registry to keep in
sync. The file is located relative to the driver's own source file, so this works with no
configuration.

```yaml
RigolDS1000Z:
  set_div_volt:
    - {status: confirmed, model: DS1054Z, firmware: "00.04.04.SP4", date: 2026-08-20, by: GG,
       notes: "front panel CH1 vertical scale read 2 V/div"}
    - {status: failed, model: DS1052E, firmware: "02.01", date: 2026-08-21, by: GG,
       notes: "command accepted, scale unchanged"}
  set_trigger_level:
    - {status: unverified}
```

Records are a **list per method, keyed by model**, because the interesting answer later is
"confirmed on the DS1054Z, never tried on the DS1052E". A schema that can't express that has to be
migrated the first time a second model is plugged in.

`roundtrip` and `confirmed` records must name a `model`, `firmware` and `date` — a bare "verified"
is not a fact, because it can neither be re-checked nor expired.

## Running the checks

### Without hardware (runs in the normal suite)

`tests/test_verification_records.py` verifies nothing about any instrument. It keeps the
bookkeeping honest, which is the part that always rots:

1. **every category-API method** is either decorated or has a record — so a newly added category
   method cannot silently have no status;
2. **capability is not restated** in YAML — one fact, one source;
3. **no record names a method that no longer exists** — catches renames, the failure mode every
   hand-maintained table dies of;
4. records use only writable statuses (`unavailable`/`unimplemented` are derived);
5. `roundtrip`/`confirmed` records carry their provenance.

This is the same pattern `InstrumentState.__init_subclass__` and `ALLOWED_ENABLEDUMMY` use
elsewhere in the suite: make omission a test failure rather than relying on discipline.

### With hardware

```bash
# round-trip mode: fast, unattended, proves self-consistency
pytest -m hardware --address=TCPIP0::192.168.1.74::INSTR --driver=RigolDS1000Z

# moderated mode: pauses at each step for a human to confirm the instrument's behaviour
pytest -m hardware --confirm --address=... --driver=...
```

Hardware tests are marked `@pytest.mark.hardware` and deselected by default, so the ordinary suite
stays runnable with no instruments attached.

In moderated mode each test prints what to look at before asking:

```
Set CH1 volts/div to 2.0 V/div.
Look at the scope: channel 1's VERTICAL scale should read 2 V/div.
(If the TIMEBASE changed instead, this driver has a set/get pair that agrees with itself
 and controls the wrong parameter.)
Confirm? [y/n/s(kip)]
```

The prompt has to name the *physical* thing to look at — that is the whole value of the mode, and
it means the prompt text is per-method content that lives with the test, not boilerplate.

A passing run writes its result back into `verification.yaml`. The writer **never lowers a
status**: a later round-trip-only run does not downgrade a previously `confirmed` method, it just
adds nothing. Failures are always recorded.

## Reading the result

```python
from constellation.verification import capability_report, summarize_report

report = capability_report(scope)             # or a driver class, no instrument needed
report["set_div_volt"]["status"]              # VerificationStatus.CONFIRMED
report["get_div_time"]["detail"]              # "DS1000E cannot query the timebase over SCPI"

summarize_report(report)                      # counts per status, for a coverage table
```

`capability_report()` merges both halves, so no caller needs to know they are stored differently.
It is what a GUI wants — grey out `unavailable`, badge `unverified` with a caution marker — and
what renders the per-driver coverage table.

## Adding a driver to the scheme

1. Create (or extend) `verification.yaml` in the driver's directory with a section named after the
   driver class.
2. Add every category-API method the driver implements, as `{status: unverified}`.
3. Decorate what the hardware cannot do with `@feature_unavailable`, and what is not written yet
   with `@feature_unimplemented` — do **not** give those YAML entries.
4. Add the class to `TRACKED_DRIVERS` in `tests/test_verification_records.py`.
5. Run the suite. It will tell you exactly which methods you missed.

Steps 2 and 5 are the ones that make this maintainable: you never have to remember the method list,
because the test computes it from the category and reports the difference.
