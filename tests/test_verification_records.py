""" Cross-checks between driver capability decorators and verification.yaml.

These run WITHOUT hardware. They don't verify anything about an instrument - they keep the
bookkeeping honest, which is the part that always rots: a method gets added to a category and no
record is created for it, or a method is renamed and its record is silently orphaned.

The pattern is the same one InstrumentState.__init_subclass__ and ALLOWED_ENABLEDUMMY use
elsewhere in this suite: make it impossible to forget by making omission a test failure.

See docs/hardware_verification.md.
"""

import pytest

from constellation.verification import (VerificationStatus, WRITABLE_STATUSES, DERIVED_STATUSES,
	verification_file_for, load_verification_records, category_api_methods, declared_capability,
	method_status, capability_report, summarize_report)
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000E_dvr import RigolDS1000E

# Drivers whose records are cross-checked. Add a driver here once its verification.yaml exists;
# until every driver is covered this is a deliberate opt-in list rather than a sweep of all
# drivers, so the un-migrated ones don't fail the suite.
TRACKED_DRIVERS = [RigolDS1000Z, RigolDS1000E]
TRACKED_IDS = [cls.__name__ for cls in TRACKED_DRIVERS]

@pytest.mark.parametrize("driver_cls", TRACKED_DRIVERS, ids=TRACKED_IDS)
def test_every_category_method_has_a_verification_story(driver_cls):
	""" Assertion 1: every category-API method is either declared in code (unavailable /
	unimplemented) or has a record in YAML. This is what stops a newly added category method from
	silently having no verification status at all. """

	records = load_verification_records(driver_cls)
	missing = []

	for name in sorted(category_api_methods(driver_cls)):
		if declared_capability(driver_cls, name)[0] is not None:
			continue
		if name not in records:
			missing.append(name)

	assert missing == [], (
		f"{driver_cls.__name__} has category methods with no verification record and no "
		f"capability decorator: {missing}. Add an entry to {verification_file_for(driver_cls)}")

@pytest.mark.parametrize("driver_cls", TRACKED_DRIVERS, ids=TRACKED_IDS)
def test_capability_is_not_restated_in_yaml(driver_cls):
	""" Assertion 2: a method the hardware can't do, or that nobody has written, has nothing to
	verify - so it must not carry a record. Two sources of truth for one fact is how they end up
	disagreeing. """

	records = load_verification_records(driver_cls)
	duplicated = [name for name in records if declared_capability(driver_cls, name)[0] is not None]

	assert duplicated == [], (
		f"{driver_cls.__name__} restates declared capability in YAML for {duplicated}. "
		f"Capability lives in the decorator only; delete these entries.")

@pytest.mark.parametrize("driver_cls", TRACKED_DRIVERS, ids=TRACKED_IDS)
def test_no_record_names_a_method_that_does_not_exist(driver_cls):
	""" Assertion 3: catches the failure mode every hand-maintained table dies of - a method gets
	renamed and its record is orphaned, still claiming a verification that now describes nothing. """

	records = load_verification_records(driver_cls)
	api = category_api_methods(driver_cls)
	orphaned = [name for name in records if name not in api and not hasattr(driver_cls, name)]

	assert orphaned == [], (
		f"{driver_cls.__name__} has records for methods that no longer exist: {orphaned}")

@pytest.mark.parametrize("driver_cls", TRACKED_DRIVERS, ids=TRACKED_IDS)
def test_records_only_use_writable_statuses(driver_cls):
	""" `unavailable`/`unimplemented` are derived from decorators and must never be written to
	file, or the two sources can disagree about the same method. """

	records = load_verification_records(driver_cls)
	bad = []

	for name, entries in records.items():
		for entry in entries:
			try:
				status = VerificationStatus(entry.get("status"))
			except ValueError:
				bad.append((name, entry.get("status"), "not a known status"))
				continue
			if status in DERIVED_STATUSES:
				bad.append((name, status.value, "derived from a decorator - cannot be written"))
			elif status not in WRITABLE_STATUSES:
				bad.append((name, status.value, "not writable"))

	assert bad == [], f"{driver_cls.__name__} has invalid records: {bad}"

@pytest.mark.parametrize("driver_cls", TRACKED_DRIVERS, ids=TRACKED_IDS)
def test_verified_records_say_what_they_were_verified_on(driver_cls):
	""" A bare `verified` is not a fact - it has to name a model, a firmware and a date, or it
	can't be re-checked or expired later. """

	records = load_verification_records(driver_cls)
	incomplete = []

	for name, entries in records.items():
		for entry in entries:
			status = entry.get("status")
			if status in ("roundtrip", "confirmed"):
				for field in ("model", "firmware", "date"):
					if not entry.get(field):
						incomplete.append((name, status, f"missing {field}"))

	assert incomplete == [], (
		f"{driver_cls.__name__} has verified records missing provenance: {incomplete}")

def test_declared_capability_distinguishes_cannot_from_not_written():
	""" The whole reason @feature_unimplemented exists: 'the hardware can't' and 'nobody wrote
	it' look identical at the call site but mean opposite things about the future. """

	status, reason = declared_capability(RigolDS1000E, "get_div_time")
	assert status == VerificationStatus.UNAVAILABLE
	assert "cannot" in reason.lower()

	status, reason = declared_capability(RigolDS1000E, "get_trigger_level")
	assert status == VerificationStatus.UNIMPLEMENTED

	# A method the driver really does implement is neither.
	assert declared_capability(RigolDS1000E, "get_div_volt")[0] is None

def test_capability_beats_records():
	""" A method the hardware cannot do has nothing to verify, whatever a file might claim. """
	status, _ = method_status(RigolDS1000E, "set_div_time")
	assert status == VerificationStatus.UNAVAILABLE

def test_unrecorded_method_reads_as_unverified_not_as_an_error():
	""" A driver with no verification.yaml at all must still report cleanly - the records are
	documentation, not a runtime dependency. """

	class _Bare(RigolDS1000Z):
		pass

	# Same directory, so it finds the real file, but has no section of its own.
	assert load_verification_records(_Bare) == {}
	assert method_status(_Bare, "set_div_volt")[0] == VerificationStatus.UNVERIFIED

@pytest.mark.parametrize("driver_cls", TRACKED_DRIVERS, ids=TRACKED_IDS)
def test_capability_report_covers_the_whole_api(driver_cls):
	""" The single call a GUI or coverage table makes: no caller should need to know that
	capability and verification live in different places. """

	report = capability_report(driver_cls)

	assert set(report) == category_api_methods(driver_cls)
	assert all(isinstance(entry["status"], VerificationStatus) for entry in report.values())

def test_report_summary_reflects_the_ds1000e_split():
	""" The DS1000E is the interesting case: four methods its hardware genuinely cannot do,
	sixteen nobody has written, and the rest implemented but unverified. """

	counts = summarize_report(capability_report(RigolDS1000E))

	assert counts[VerificationStatus.UNAVAILABLE] == 4
	assert counts[VerificationStatus.UNIMPLEMENTED] == 16
	assert counts[VerificationStatus.UNVERIFIED] == 7
	assert counts[VerificationStatus.CONFIRMED] == 0

def test_model_filter_selects_the_right_record(tmp_path):
	""" One driver covers a series, so records are per (method, model). Asking about a DS1052E
	must not return the DS1054Z's result. """
	import yaml

	records = {
		"RigolDS1000Z": {
			"set_div_volt": [
				{"status": "confirmed", "model": "DS1054Z", "firmware": "00.04.04", "date": "2026-01-01", "by": "tester"},
				{"status": "failed", "model": "DS1052E", "firmware": "02.01", "date": "2026-01-02", "by": "tester"},
			]
		}
	}

	path = tmp_path / "verification.yaml"
	path.write_text(yaml.safe_dump(records), encoding="utf-8")

	import constellation.verification as cv
	original = cv.verification_file_for
	cv.verification_file_for = lambda cls: str(path)
	try:
		assert cv.method_status(RigolDS1000Z, "set_div_volt", model="DS1054Z")[0] == VerificationStatus.CONFIRMED
		assert cv.method_status(RigolDS1000Z, "set_div_volt", model="DS1052E")[0] == VerificationStatus.FAILED
	finally:
		cv.verification_file_for = original

def test_a_failure_is_not_outranked_into_silence(tmp_path):
	""" `failed` is evidence of not working, not weak evidence of working - it must not be hidden
	by an older passing record for a different model. """
	import yaml

	records = {"RigolDS1000Z": {"set_div_volt": [
		{"status": "failed", "model": "DS1052E", "firmware": "02.01", "date": "2026-01-02", "by": "tester"},
	]}}
	path = tmp_path / "verification.yaml"
	path.write_text(yaml.safe_dump(records), encoding="utf-8")

	import constellation.verification as cv
	original = cv.verification_file_for
	cv.verification_file_for = lambda cls: str(path)
	try:
		assert cv.method_status(RigolDS1000Z, "set_div_volt")[0] == VerificationStatus.FAILED
	finally:
		cv.verification_file_for = original
