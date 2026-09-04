''' Writes hardware-verification records back into `verification.yaml`.

Kept apart from `verification.py` deliberately: reading records is something a GUI, a coverage
table or a user does constantly, and it must not drag in the ability to rewrite the file. Only the
hardware test suite imports this module.

Three rules govern every write, and all three exist because of a way this kind of file rots:

  1. **Never lower a status within the same code.** A later round-trip-only run must not downgrade
     a method a human already confirmed - otherwise an unattended run quietly destroys the
     expensive evidence. "Within the same code" is the important qualifier: an old `confirmed`
     record cannot lend its strength to a method whose implementation has since changed, so when
     the code hash differs the new record replaces the old outright rather than inheriting from
     it. Laundering unconfirmed code as human-confirmed is exactly the failure the staleness layer
     exists to prevent.
  2. **Always record failures.** "We tried it and it broke" is more useful than "nothing is
     known", and a fresh failure replaces an earlier pass *for the same instrument* so a
     regression is never masked by yesterday's success.
  3. **Never destroy the file's header.** `yaml.safe_dump` of the whole document would delete the
     explanatory comment block that tells the next person what the file is and what must not be
     written into it. The header is re-emitted verbatim and only the record blocks are generated.

See docs/hardware_verification.md.
'''

import datetime
import os

from constellation.verification import (VerificationStatus, WRITABLE_STATUSES, VERIFICATION_EPOCH,
	_STATUS_STRENGTH, _yaml, declared_capability, method_code_hash, verification_file_for)

# Order fields are emitted in. Status first because it is what a person scans for; provenance
# after it; the free-text note last.
_FIELD_ORDER = ("status", "model", "firmware", "idn", "code_hash", "epoch", "date", "by", "note")

def _firmware_from_idn(idn:str):
	''' Pulls the firmware field out of a SCPI *IDN? response ("<vendor>,<model>,<serial>,<fw>").

	Stored separately as well as inside `idn` because it is the field a human scans for when asking
	"was this ever checked on the firmware I have", and because the coverage table shows it.
	'''

	if not idn:
		return None

	parts = [p.strip() for p in idn.split(",")]

	return parts[3] if len(parts) > 3 else None

def build_record(driver_cls, name:str, status:VerificationStatus, model:str=None, idn:str=None, by:str=None, note:str=None, date:str=None, firmware:str=None) -> dict:
	''' Assembles one record, stamping everything needed to invalidate it later.

	The provenance fields are gathered here rather than by the caller so that a record physically
	cannot be written without them - a record that cannot expire is worse than no record, because
	it will eventually be believed about code it never described.

	Args:
		driver_cls (type): The Driver subclass the record belongs to.
		name (str): Method name.
		status (VerificationStatus): Must be a writable status - the derived ones (unavailable,
			unimplemented, stale-*) are computed from code and must never be frozen into a file.
		model (str): Instrument model, e.g. "DS1054Z". One driver covers a series, and a command
			can work on one member and not another.
		idn (str): The instrument's full `*IDN?` string, for the firmware staleness check.
		by (str): Operator, for a confirmed record. Defaults to the `USER` environment variable.
		firmware (str): Overrides the firmware version, which is otherwise read out of `idn`.
		note (str): Optional free text - a caveat, a workaround, why a failure failed.
		date (str): ISO date. Defaults to today.

	Returns:
		dict: The record.

	Raises:
		ValueError: If `status` is not writable, or the method carries a capability decorator (a
			method the hardware cannot do, or that nobody has written, has nothing to verify).
	'''

	if status not in WRITABLE_STATUSES:
		raise ValueError(f"Refusing to write derived status >{status.value}< for {driver_cls.__name__}.{name}(). Derived statuses are computed from the code, not stored.")

	declared, reason = declared_capability(driver_cls, name)
	if declared is not None:
		raise ValueError(f"Refusing to write a verification record for {driver_cls.__name__}.{name}(): it is declared {declared.value} in code ({reason}). Capability lives in the decorator only.")

	record = {
		"status": status.value,
		"model": model,
		"firmware": firmware or _firmware_from_idn(idn),
		"idn": idn,
		"code_hash": method_code_hash(driver_cls, name),
		"epoch": VERIFICATION_EPOCH,
		"date": date or datetime.date.today().isoformat(),
		"by": by or os.environ.get("USER") or "unknown",
	}

	if note:
		record["note"] = note

	# A record with no model or IDN can't be matched against a specific instrument later. That is
	# tolerable (the fields stay None and the firmware check simply can't run), so it is not an
	# error - but the fields are always present, so the shape never varies.
	return record

def _same_instrument(a:dict, b:dict) -> bool:
	''' Whether two records describe the same physical instrument.

	IDN is the identity when both records carry one - it distinguishes two DS1054Zs at different
	firmware, which is precisely the case the firmware check cares about. Otherwise model is the
	best available answer.
	'''

	if a.get("idn") and b.get("idn"):
		return a["idn"].strip() == b["idn"].strip()

	return a.get("model") == b.get("model")

def _is_placeholder(record:dict) -> bool:
	''' Whether a record is a seeded "nothing is known here" entry rather than evidence. '''

	return record.get("status") == VerificationStatus.UNVERIFIED.value and not record.get("idn") and not record.get("code_hash")

def _strength(record:dict) -> int:
	try:
		return _STATUS_STRENGTH.get(VerificationStatus(record.get("status")), -1)
	except ValueError:
		return -1

def merge_record(existing:list, new:dict) -> tuple:
	''' Folds one new record into a method's existing record list.

	Args:
		existing (list): The records currently on file for this method.
		new (dict): The record from this run.

	Returns:
		tuple: (merged_list, action), where action is one of "added", "replaced" or "kept".
			"kept" means the new record was discarded because the existing one is stronger
			evidence about the same code on the same instrument.
	'''

	new_status = new.get("status")

	# `{status: unverified}` entries are seeded placeholders meaning "we have nothing on file", not
	# evidence about an instrument. They carry no model or IDN, so they would never match a real
	# record and would sit in the file forever next to it, saying the opposite thing. Any real
	# result retires them.
	merged = [record for record in existing
		if not (new_status != VerificationStatus.UNVERIFIED.value and _is_placeholder(record))]

	for i, record in enumerate(merged):

		if not _same_instrument(record, new):
			continue

		# A fresh failure always wins. Rule 2: a regression must never be hidden behind an
		# earlier pass on the same instrument.
		if new_status == VerificationStatus.FAILED.value:
			merged[i] = new
			return merged, "replaced"

		# It works now. The old failure has been superseded on this instrument.
		if record.get("status") == VerificationStatus.FAILED.value:
			merged[i] = new
			return merged, "replaced"

		# Different code, or gathered under different plumbing - the old record is evidence about
		# something else and cannot lend its status to this run.
		if record.get("code_hash") != new.get("code_hash") or record.get("epoch") != new.get("epoch"):
			merged[i] = new
			return merged, "replaced"

		# Same code, same instrument: rule 1. Keep whichever is stronger, and keep it *whole* - a
		# round-trip run must not restamp a human-confirmed record with today's date, because
		# nobody looked at the front panel today.
		if _strength(record) >= _strength(new):
			return merged, "kept"

		merged[i] = new
		return merged, "replaced"

	merged.append(new)

	# A retired placeholder means the file did change, even though nothing was matched.
	return merged, ("replaced" if len(merged) <= len(existing) else "added")

def _split_header(text:str) -> str:
	''' Returns the leading comment block of a YAML file, including its trailing blank line.

	Everything from the first line that is neither blank nor a comment onwards is generated
	content and is discarded on rewrite.
	'''

	lines = []

	for line in text.splitlines():
		stripped = line.strip()
		if stripped and not stripped.startswith("#"):
			break
		lines.append(line)

	# Drop trailing blanks, then re-add exactly one, so repeated writes don't accumulate them.
	while lines and not lines[-1].strip():
		lines.pop()

	return ("\n".join(lines) + "\n\n") if lines else ""

def _emit_record(record:dict) -> str:
	''' One record as a single-line YAML flow mapping, in a stable field order.

	Hand-built rather than dumped wholesale so the field order is meaningful to a human reader,
	but the *values* still go through the YAML dumper - quoting an IDN string containing a comma
	is not something to reimplement.
	'''

	yaml = _yaml()

	ordered = [key for key in _FIELD_ORDER if key in record]
	ordered += [key for key in record if key not in _FIELD_ORDER]

	body = yaml.safe_dump({key: record[key] for key in ordered}, default_flow_style=True, sort_keys=False, width=10**9, allow_unicode=True).strip()

	# safe_dump wraps flow mappings in {...} already; guard in case of a degenerate empty record.
	return body if body.startswith("{") else "{" + body + "}"

def render_records_file(header:str, document:dict) -> str:
	''' Renders a whole verification.yaml: the preserved header, then one block per driver.

	Args:
		header (str): Text to place at the top, verbatim.
		document (dict): {driver_name: {method_name: [record, ...]}}.

	Returns:
		str: The file contents.
	'''

	out = [header] if header else []

	for driver_name in sorted(document):

		out.append(f"{driver_name}:\n")

		methods = document[driver_name] or {}
		for method_name in sorted(methods):

			records = methods[method_name]
			records = records if isinstance(records, list) else [records]

			out.append(f"  {method_name}:\n")
			for record in records:
				out.append(f"    - {_emit_record(record)}\n")

		out.append("\n")

	return "".join(out).rstrip("\n") + "\n"

def update_records(driver_cls, new_records:dict, path:str=None) -> dict:
	''' Merges a run's results into the driver's verification.yaml and rewrites it.

	Args:
		driver_cls (type): The Driver subclass that was tested.
		new_records (dict): {method_name: record}, as built by build_record().
		path (str): Override the file location. Defaults to the file beside the driver's source.

	Returns:
		dict: {method_name: action} - "added", "replaced" or "kept" per method, so a run can
			report what it actually changed rather than claiming to have written everything.
	'''

	path = path or verification_file_for(driver_cls)
	yaml = _yaml()

	if os.path.exists(path):
		with open(path, "r", encoding="utf-8") as f:
			text = f.read()
		header = _split_header(text)
		document = yaml.safe_load(text) or {}
	else:
		header = ""
		document = {}

	section = document.setdefault(driver_cls.__name__, {}) or {}
	document[driver_cls.__name__] = section

	actions = {}
	for name, record in new_records.items():

		existing = section.get(name, [])
		existing = existing if isinstance(existing, list) else [existing]

		section[name], actions[name] = merge_record(existing, record)

	with open(path, "w", encoding="utf-8") as f:
		f.write(render_records_file(header, document))

	return actions
