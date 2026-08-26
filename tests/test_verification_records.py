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
	STALE_STATUSES, VERIFIED_STATUSES, VERIFICATION_EPOCH, FRAMEWORK_CRITICAL_FUNCTIONS,
	verification_file_for, load_verification_records, category_api_methods, declared_capability,
	method_status, capability_report, summarize_report, method_code_hash, framework_fingerprint,
	record_staleness)
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
				# code_hash and epoch are what make the record expirable; without them the claim
				# can never go stale, which is worse than not having it.
				for field in ("model", "firmware", "date", "code_hash", "epoch"):
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

# ---------------------------------------------------------------------------------------------
# Staleness
#
# A verification record is evidence with an expiry, not a certificate. The failure this layer
# exists to prevent: a driver is verified on hardware, then changed, and a later user reads
# "confirmed" and trusts it.
# ---------------------------------------------------------------------------------------------

# Recomputed whenever the shared instrument-I/O path legitimately changes. When this test fails,
# decide whether the change could alter what drivers actually do to hardware:
#   - yes -> bump VERIFICATION_EPOCH in verification.py, then update this constant;
#   - no  -> just update this constant.
# Either way the decision is recorded, which is the point. See docs/hardware_verification.md.
EXPECTED_FRAMEWORK_FINGERPRINT = "fc220ace5e6e2e22"
EXPECTED_EPOCH = 1

def _record(status="confirmed", **kw):
	base = {
		"status": status,
		"idn": "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA00000000,00.04.04.SP4",
		"code_hash": method_code_hash(RigolDS1000Z, "set_div_volt"),
		"epoch": VERIFICATION_EPOCH,
		"date": "2026-08-26",
		"by": "tester",
		"model": "DS1054Z",
		"firmware": "00.04.04.SP4",
	}
	base.update(kw)
	return base

def test_framework_fingerprint_has_not_changed_without_an_epoch_decision():
	""" The automated half of framework staleness. It does not decide whether a plumbing change
	invalidates hardware evidence - that isn't machine-decidable - it just makes it impossible to
	change the shared I/O path without someone consciously deciding. """
	assert framework_fingerprint() == EXPECTED_FRAMEWORK_FINGERPRINT, (
		"The shared instrument-I/O path changed. Decide whether that could alter what drivers do "
		"to hardware: if so bump VERIFICATION_EPOCH, then update EXPECTED_FRAMEWORK_FINGERPRINT.")
	assert VERIFICATION_EPOCH == EXPECTED_EPOCH

def test_framework_critical_list_resolves():
	""" A renamed function would silently drop out of the fingerprint, quietly weakening the
	guard rather than failing it. """
	import constellation.verification as cv
	for module_name, dotted in FRAMEWORK_CRITICAL_FUNCTIONS:
		assert cv._resolve_dotted(module_name, dotted) is not None

def test_method_hash_ignores_formatting_but_not_behaviour():
	""" Hashing raw source would invalidate evidence on a comment or a docstring fix, which
	trains people to ignore staleness. Hashing the AST does not. """
	import constellation.verification as cv

	def original(self, channel):
		return float(self.query(f":CHAN{channel}:SCAL?"))

	def reformatted(self, channel):
		''' A docstring that did not exist before. '''
		# ...and a comment.
		return float(self.query(f":CHAN{channel}:SCAL?"))

	def changed(self, channel):
		return float(self.query(f":CHAN{channel}:OFFS?"))     # different SCPI command

	assert cv._normalized_source_hash(original) == cv._normalized_source_hash(reformatted)
	assert cv._normalized_source_hash(original) != cv._normalized_source_hash(changed)

def test_method_hash_is_stable_and_specific():
	first = method_code_hash(RigolDS1000Z, "set_div_volt")
	assert first == method_code_hash(RigolDS1000Z, "set_div_volt")
	assert first != method_code_hash(RigolDS1000Z, "get_div_volt")
	assert first != ""

def test_a_current_record_is_not_stale():
	assert record_staleness(RigolDS1000Z, "set_div_volt", _record()) is None

def test_changed_method_code_makes_a_record_stale():
	""" Case (2): driver implemented and confirmed, then changed. """
	stale = _record(code_hash="0000000000000000")
	assert record_staleness(RigolDS1000Z, "set_div_volt", stale) == VerificationStatus.STALE_CODE

def test_an_older_epoch_makes_a_record_stale():
	""" The method's own source is untouched, but the plumbing underneath it changed. """
	stale = _record(epoch=VERIFICATION_EPOCH - 1)
	assert record_staleness(RigolDS1000Z, "set_div_volt", stale) == VerificationStatus.STALE_FRAMEWORK

def test_a_different_instrument_makes_a_record_stale():
	live = "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA00000000,00.05.00"     # firmware updated
	assert record_staleness(RigolDS1000Z, "set_div_volt", _record(), idn=live) == VerificationStatus.STALE_FIRMWARE

def test_firmware_staleness_is_not_guessed_when_no_instrument_is_connected():
	""" Absence of information is not evidence of staleness - an offline report must not cry wolf
	on every record. """
	assert record_staleness(RigolDS1000Z, "set_div_volt", _record(), idn=None) is None

@pytest.mark.parametrize("status", ["unverified", "failed"])
def test_only_claims_of_success_can_go_stale(status):
	""" There is nothing to invalidate about 'never tried', and a failure does not become less of
	a failure because the code moved on. """
	record = _record(status=status, code_hash="0000000000000000", epoch=VERIFICATION_EPOCH - 1)
	assert record_staleness(RigolDS1000Z, "set_div_volt", record) is None

def test_stale_record_is_reported_as_stale_not_as_confirmed(tmp_path, monkeypatch):
	""" The whole point: a user must never read 'confirmed' for code that has since changed. """
	import yaml
	import constellation.verification as cv

	records = {"RigolDS1000Z": {"set_div_volt": [_record(code_hash="0000000000000000")]}}
	path = tmp_path / "verification.yaml"
	path.write_text(yaml.safe_dump(records), encoding="utf-8")
	monkeypatch.setattr(cv, "verification_file_for", lambda cls: str(path))

	status, detail = cv.method_status(RigolDS1000Z, "set_div_volt")

	assert status == VerificationStatus.STALE_CODE
	# The expired record is still handed back - "this worked at hash X on date Y" beats a bare
	# "unverified", and it distinguishes 'used to work' from 'never tried'.
	assert detail["date"] == "2026-08-26"

def test_a_valid_record_outranks_a_stale_one(tmp_path, monkeypatch):
	import yaml
	import constellation.verification as cv

	records = {"RigolDS1000Z": {"set_div_volt": [
		_record(code_hash="0000000000000000", model="DS1052E"),      # stale
		_record(status="roundtrip"),                                  # current
	]}}
	path = tmp_path / "verification.yaml"
	path.write_text(yaml.safe_dump(records), encoding="utf-8")
	monkeypatch.setattr(cv, "verification_file_for", lambda cls: str(path))

	assert cv.method_status(RigolDS1000Z, "set_div_volt")[0] == VerificationStatus.ROUNDTRIP

def test_report_marks_only_valid_claims_as_trusted(tmp_path, monkeypatch):
	""" `trusted` is the single field a UI needs. Everything uncertain - unverified, stale,
	failed, unimplemented - must be False. Fail closed. """
	import yaml
	import constellation.verification as cv

	records = {"RigolDS1000Z": {
		"set_div_volt": [_record()],                                   # valid
		"get_div_volt": [_record(code_hash="0000000000000000")],       # stale
		"set_coupling": [_record(status="failed")],                    # failed
	}}
	path = tmp_path / "verification.yaml"
	path.write_text(yaml.safe_dump(records), encoding="utf-8")
	monkeypatch.setattr(cv, "verification_file_for", lambda cls: str(path))

	report = cv.capability_report(RigolDS1000Z)

	assert report["set_div_volt"]["trusted"] is True
	assert report["get_div_volt"]["trusted"] is False
	assert report["set_coupling"]["trusted"] is False
	assert report["set_trigger_level"]["trusted"] is False      # no record at all
	assert all(entry["trusted"] is False
		for entry in cv.capability_report(RigolDS1000E).values())

def test_report_uses_a_connected_instruments_idn_automatically():
	""" A report taken against real hardware should notice records made against another firmware
	without the caller having to pass anything. """
	import pylogfile.base as plf
	from constellation.relay import DirectSCPIRelay

	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL
	scope = RigolDS1000Z("DUMMY", log=log, relay=DirectSCPIRelay(), dummy=True)
	scope.id.idn_model = "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA00000000,99.99.99"

	# Nothing is verified yet, so this asserts the plumbing rather than a specific verdict.
	report = capability_report(scope)
	assert set(report) == category_api_methods(RigolDS1000Z)

def test_stale_statuses_are_all_derived_and_unwritable():
	""" Staleness is computed by comparing a record to reality; writing it into a file would
	freeze a judgement that is supposed to be recomputed every time. """
	assert STALE_STATUSES <= DERIVED_STATUSES
	assert not (STALE_STATUSES & WRITABLE_STATUSES)
	assert not (STALE_STATUSES & VERIFIED_STATUSES)
