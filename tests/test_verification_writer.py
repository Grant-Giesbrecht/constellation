""" The record writer's rules, checked without hardware.

The hardware suite can only be run by someone standing at a bench, so the part that decides what
gets written - and, more importantly, what must NOT be overwritten - is pinned down here instead.
Every test below corresponds to a way this file rots in practice:

  - an unattended round-trip run silently destroying a human's confirmation,
  - an old confirmation being inherited by code that has since changed,
  - a regression being masked by yesterday's pass,
  - yaml.safe_dump wiping the header that explains the file.

See docs/hardware_verification.md.
"""

import pytest
import yaml

from constellation.verification import (VerificationStatus, VERIFICATION_EPOCH, method_code_hash,
	method_status, load_verification_records)
from constellation.verification_writer import (build_record, merge_record, update_records,
	render_records_file, _split_header)
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000E_dvr import RigolDS1000E

IDN_A = "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA000000001,00.04.04"
IDN_B = "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA000000002,00.04.05"

def _record(status="confirmed", code_hash="aaaaaaaaaaaaaaaa", epoch=VERIFICATION_EPOCH, idn=IDN_A, model="DS1054Z", date="2026-08-01"):

	return {"status": status, "model": model, "idn": idn, "code_hash": code_hash,
		"epoch": epoch, "date": date, "by": "someone"}

# --- build_record ----------------------------------------------------------------------------

def test_build_record_stamps_everything_needed_to_expire_it():
	""" A record that cannot be invalidated is worse than no record - it will eventually be
	believed about code it never described. """

	record = build_record(RigolDS1000Z, "set_div_volt", VerificationStatus.ROUNDTRIP, model="DS1054Z", idn=IDN_A, by="tester")

	assert record["status"] == "roundtrip"
	assert record["code_hash"] == method_code_hash(RigolDS1000Z, "set_div_volt")
	assert record["epoch"] == VERIFICATION_EPOCH
	assert record["idn"] == IDN_A
	assert record["date"] and record["by"] == "tester"

@pytest.mark.parametrize("status", [VerificationStatus.UNAVAILABLE, VerificationStatus.UNIMPLEMENTED,
	VerificationStatus.STALE_CODE, VerificationStatus.STALE_FRAMEWORK, VerificationStatus.STALE_FIRMWARE])
def test_derived_statuses_cannot_be_written(status):
	""" Derived statuses are recomputed from the code and the connected instrument every time.
	Freezing one into a file turns a live check into a stale assertion. """

	with pytest.raises(ValueError):
		build_record(RigolDS1000Z, "set_div_volt", status)

def test_a_method_declared_unavailable_cannot_be_recorded():
	""" The DS1000E cannot set its timebase over SCPI at all. There is nothing to verify, and
	restating the fact in YAML gives it two sources of truth to disagree between. """

	with pytest.raises(ValueError):
		build_record(RigolDS1000E, "set_div_time", VerificationStatus.ROUNDTRIP)

# --- merge rules -----------------------------------------------------------------------------

def test_a_roundtrip_run_does_not_downgrade_a_confirmation():
	""" Rule 1. An unattended run must not destroy evidence that cost a human their time. """

	existing = [_record(status="confirmed")]
	new = _record(status="roundtrip", date="2026-08-26")

	merged, action = merge_record(existing, new)

	assert action == "kept"
	assert merged[0]["status"] == "confirmed"
	# And it is kept WHOLE - restamping today's date would claim someone looked at the front panel
	# today, which nobody did.
	assert merged[0]["date"] == "2026-08-01"

def test_a_confirmation_does_not_survive_onto_changed_code():
	""" The other half of rule 1, and the one that matters more: an old confirmation must not be
	inherited by an implementation that has since changed. """

	existing = [_record(status="confirmed", code_hash="aaaaaaaaaaaaaaaa")]
	new = _record(status="roundtrip", code_hash="bbbbbbbbbbbbbbbb")

	merged, action = merge_record(existing, new)

	assert action == "replaced"
	assert merged[0]["status"] == "roundtrip"
	assert merged[0]["code_hash"] == "bbbbbbbbbbbbbbbb"

def test_a_confirmation_upgrades_a_roundtrip_on_the_same_code():

	merged, action = merge_record([_record(status="roundtrip")], _record(status="confirmed"))

	assert action == "replaced"
	assert merged[0]["status"] == "confirmed"

def test_a_fresh_failure_replaces_a_pass_on_the_same_instrument():
	""" Rule 2. A regression that stays hidden behind yesterday's success is the worst outcome
	this whole scheme is trying to avoid. """

	merged, action = merge_record([_record(status="confirmed")], _record(status="failed"))

	assert action == "replaced"
	assert merged[0]["status"] == "failed"
	assert len(merged) == 1

def test_a_pass_supersedes_an_earlier_failure():

	merged, action = merge_record([_record(status="failed")], _record(status="roundtrip"))

	assert action == "replaced"
	assert merged[0]["status"] == "roundtrip"

def test_a_different_instrument_gets_its_own_record():
	""" One driver covers a series, and a command can work on one member and not another - so
	records accumulate per instrument rather than overwriting each other. """

	merged, action = merge_record([_record(idn=IDN_A)], _record(idn=IDN_B, status="failed"))

	assert action == "added"
	assert len(merged) == 2

def test_an_unverified_placeholder_is_replaced():
	""" The seeded `{status: unverified}` entries are placeholders, not evidence. """

	merged, action = merge_record([{"status": "unverified"}], _record(status="roundtrip"))

	assert action == "replaced"
	assert len(merged) == 1
	assert merged[0]["status"] == "roundtrip"

# --- file rendering --------------------------------------------------------------------------

HEADER = "# Explanatory header.\n# Second line.\n"

def test_the_header_survives_a_rewrite(tmp_path):
	""" `yaml.safe_dump` of the whole document would delete the comment block telling the next
	person what this file is and what must never be written into it. """

	path = tmp_path / "verification.yaml"
	path.write_text(HEADER + "\nRigolDS1000Z:\n  set_div_volt:\n    - {status: unverified}\n", encoding="utf-8")

	update_records(RigolDS1000Z, {"set_div_volt": build_record(RigolDS1000Z, "set_div_volt", VerificationStatus.ROUNDTRIP, model="DS1054Z", idn=IDN_A)}, path=str(path))

	text = path.read_text(encoding="utf-8")

	assert text.startswith(HEADER)
	assert "roundtrip" in text

def test_rewrites_are_stable(tmp_path):
	""" Writing the same result twice must produce a byte-identical file - otherwise every run
	shows up as a diff and the real changes get lost in the noise. """

	path = tmp_path / "verification.yaml"
	path.write_text(HEADER + "\nRigolDS1000Z: {}\n", encoding="utf-8")

	record = build_record(RigolDS1000Z, "set_div_volt", VerificationStatus.ROUNDTRIP, model="DS1054Z", idn=IDN_A)

	update_records(RigolDS1000Z, {"set_div_volt": record}, path=str(path))
	first = path.read_text(encoding="utf-8")

	update_records(RigolDS1000Z, {"set_div_volt": record}, path=str(path))

	assert path.read_text(encoding="utf-8") == first

def test_other_drivers_sections_are_left_intact(tmp_path):
	""" One file holds every driver in a directory. Testing one scope must not blank the other. """

	path = tmp_path / "verification.yaml"
	path.write_text(HEADER + "\nRigolDS1000E:\n  set_div_volt:\n    - {status: unverified}\n", encoding="utf-8")

	update_records(RigolDS1000Z, {"set_div_volt": build_record(RigolDS1000Z, "set_div_volt", VerificationStatus.ROUNDTRIP)}, path=str(path))

	document = yaml.safe_load(path.read_text(encoding="utf-8"))

	assert document["RigolDS1000E"]["set_div_volt"][0]["status"] == "unverified"
	assert document["RigolDS1000Z"]["set_div_volt"][0]["status"] == "roundtrip"

def test_an_idn_containing_commas_survives_the_round_trip():
	""" A SCPI IDN string is comma-separated, which is exactly what breaks a hand-rolled flow-style
	emitter. The values go through the YAML dumper for this reason. """

	text = render_records_file("", {"RigolDS1000Z": {"set_div_volt": [_record()]}})

	assert yaml.safe_load(text)["RigolDS1000Z"]["set_div_volt"][0]["idn"] == IDN_A

def test_split_header_stops_at_the_first_content_line():

	assert _split_header("# a\n# b\n\nRigolDS1000Z:\n  x: 1\n") == "# a\n# b\n\n"
	assert _split_header("RigolDS1000Z:\n") == ""

# --- end to end ------------------------------------------------------------------------------

def test_a_written_record_reads_back_as_verified(tmp_path, monkeypatch):
	""" The whole loop: a run stamps the file, and a later reader sees a trusted claim - because
	the hash the writer stamped is the hash the reader computes. If these two ever disagree, every
	record is born stale and the feature is silently useless. """

	import constellation.verification as cv

	path = tmp_path / "verification.yaml"
	path.write_text(HEADER + "\n", encoding="utf-8")

	update_records(RigolDS1000Z, {"set_div_volt": build_record(RigolDS1000Z, "set_div_volt", VerificationStatus.CONFIRMED, model="DS1054Z", idn=IDN_A)}, path=str(path))

	monkeypatch.setattr(cv, "verification_file_for", lambda cls: str(path))

	assert load_verification_records(RigolDS1000Z)["set_div_volt"][0]["status"] == "confirmed"

	status, detail = method_status(RigolDS1000Z, "set_div_volt", idn=IDN_A)
	assert status == VerificationStatus.CONFIRMED

	# ...and the same record against a different firmware is not trusted.
	assert method_status(RigolDS1000Z, "set_div_volt", idn=IDN_B)[0] == VerificationStatus.STALE_FIRMWARE

def test_a_built_record_satisfies_the_provenance_cross_check():
	""" Closes the loop between the writer and the checker: `test_verification_records.py` rejects
	a verified record missing any of these, so a run that produced one would stamp records its own
	suite refuses. """

	record = build_record(RigolDS1000Z, "set_div_volt", VerificationStatus.CONFIRMED, model="DS1054Z", idn=IDN_A)

	for field in ("model", "firmware", "date", "code_hash", "epoch"):
		assert record.get(field), f"build_record() omitted {field}"

	assert record["firmware"] == "00.04.04"
