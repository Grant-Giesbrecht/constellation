''' Hardware-verification records: which driver methods have actually been checked against a
physical instrument, on which model, and how thoroughly.

The problem this solves: nothing in a repository tells you whether `set_trigger_level()` was ever
run against a real scope. Git history answers "was this reviewed", not "does this SCPI string do
what we think on a DS1054Z at firmware 00.04.04". That fact is per-method, per-model, perishable
(a firmware update can invalidate it), and it is what a person picking up a driver most wants to
know.

Two halves, deliberately kept apart:

  - **capability** lives in code, as `@feature_unavailable` / `@feature_unimplemented` decorators
    on the driver. Whether an instrument *can* do something is a property of the instrument, and
    belongs next to the method.
  - **verification** lives in `verification.yaml` beside the drivers it describes. Whether an
    implementation has been *checked* is an event with a date, a model and an operator, and is
    written by the hardware test run rather than typed by hand.

Capability is never restated in YAML - it is derived. See docs/hardware_verification.md.
'''

import inspect
import os
from enum import Enum

import pylogfile.base as plf

from constellation.base import Driver

VERIFICATION_FILENAME = "verification.yaml"

class VerificationStatus(Enum):
	''' How thoroughly one driver method has been checked against real hardware.

	The two "verified" flavours exist because a round trip can be self-consistently wrong. If a
	driver's setter writes the timebase and its getter also reads the timebase, then setting
	volts/div and reading it back agrees perfectly - and the driver is broken. Only a person
	looking at the instrument's front panel can catch that, so a machine-checked round trip and a
	human-confirmed one are different facts and are recorded differently.

	Some methods can *only* be CONFIRMED: an action command (run/stop acquisition, single
	trigger, preset) has no read-back at all, so ROUNDTRIP is not merely weaker for those, it is
	unreachable.

	UNAVAILABLE:   derived from @feature_unavailable - the hardware cannot do this. Permanent.
	UNIMPLEMENTED: derived from @feature_unimplemented - not written yet. A work queue.
	UNVERIFIED:    implemented, never checked against hardware.
	ROUNDTRIP:     set/read-back agreed on real hardware. Proves the set/get pair is
	               self-consistent - NOT that it controls the parameter it claims to.
	CONFIRMED:     a person watched the instrument and confirmed the physical effect.
	FAILED:        checked against hardware and did not work. Distinct from never having tried,
	               and much more useful.
	'''

	UNAVAILABLE = "unavailable"
	UNIMPLEMENTED = "unimplemented"
	UNVERIFIED = "unverified"
	ROUNDTRIP = "roundtrip"
	CONFIRMED = "confirmed"
	FAILED = "failed"

# Strength ordering, used when a method has records from several models/runs: the report shows the
# strongest. FAILED is deliberately absent - it is not "weak evidence of working", it is evidence
# of not working, and is surfaced separately rather than being outranked into silence.
_STATUS_STRENGTH = {
	VerificationStatus.UNVERIFIED: 0,
	VerificationStatus.ROUNDTRIP: 1,
	VerificationStatus.CONFIRMED: 2,
}

# Statuses a YAML record may carry. The derived ones are computed from decorators and must never
# be written into a file - a cross-check test enforces that.
WRITABLE_STATUSES = {
	VerificationStatus.UNVERIFIED,
	VerificationStatus.ROUNDTRIP,
	VerificationStatus.CONFIRMED,
	VerificationStatus.FAILED,
}

DERIVED_STATUSES = {
	VerificationStatus.UNAVAILABLE,
	VerificationStatus.UNIMPLEMENTED,
}

# Abstract methods declared by Driver itself. These are framework plumbing implemented by the
# category class, not instrument commands a driver author writes, so they are not part of what
# gets verified.
_FRAMEWORK_ABSTRACTS = {"refresh_state", "apply_state", "refresh_data"}

def _yaml():
	''' Imports PyYAML on demand, with an error that says what to install. '''

	try:
		import yaml
	except ImportError as e:
		raise ImportError(f"Hardware-verification records need PyYAML (`pip install pyyaml`). ({e})")

	return yaml

def verification_file_for(driver_cls) -> str:
	''' Path of the verification.yaml that describes `driver_cls`.

	Resolved relative to the driver's own source file, so a driver living in a different
	repository (see the planned split of VICP/DAQmx/zhinst drivers) automatically uses that
	repository's records, with no registry to keep in sync.

	Args:
		driver_cls (type): A Driver subclass.

	Returns:
		str: Absolute path to the file. May not exist.
	'''

	return os.path.join(os.path.dirname(os.path.abspath(inspect.getfile(driver_cls))), VERIFICATION_FILENAME)

def load_verification_records(driver_cls) -> dict:
	''' Reads the records for one driver class.

	Args:
		driver_cls (type): A Driver subclass.

	Returns:
		dict: {method_name: [record, ...]}. Empty if the file or the driver's section is absent.
			Each record is a dict with at least `status`; verified records also carry `model`,
			`firmware`, `date` and `by`.
	'''

	path = verification_file_for(driver_cls)

	if not os.path.exists(path):
		return {}

	with open(path, "r", encoding="utf-8") as f:
		contents = _yaml().safe_load(f) or {}

	records = contents.get(driver_cls.__name__, {}) or {}

	# A single record may be written without the surrounding list, since that is the common case.
	return {name: (rec if isinstance(rec, list) else [rec]) for name, rec in records.items()}

def category_api_methods(driver_cls) -> set:
	''' Every method of the driver's category API - i.e. the set that needs a verification story.

	Collected by walking the MRO for methods declared abstract by a category class. Driver's own
	abstract methods (refresh_state/apply_state/refresh_data) are excluded: they are framework
	plumbing implemented by the category, not instrument commands.

	Args:
		driver_cls (type): A Driver subclass.

	Returns:
		set: Method names.
	'''

	names = set()

	for cls in driver_cls.__mro__:

		if cls is Driver:
			continue

		for name, obj in vars(cls).items():

			if name in _FRAMEWORK_ABSTRACTS:
				continue

			fn = getattr(obj, "__func__", obj)
			if getattr(fn, "__isabstractmethod__", False) or getattr(obj, "__isabstractmethod__", False):
				names.add(name)

	return names

def declared_capability(driver_cls, name:str):
	''' The capability status a decorator declares for one method, if any.

	Args:
		driver_cls (type): A Driver subclass.
		name (str): Method name.

	Returns:
		tuple: (VerificationStatus, reason) for a decorated method, or (None, None).
	'''

	attr = inspect.getattr_static(driver_cls, name, None)

	reason = getattr(attr, "__feature_unavailable__", None)
	if reason is not None:
		return VerificationStatus.UNAVAILABLE, reason

	reason = getattr(attr, "__feature_unimplemented__", None)
	if reason is not None:
		return VerificationStatus.UNIMPLEMENTED, reason

	return None, None

def method_status(driver_cls, name:str, model:str=None) -> tuple:
	''' Resolves one method's status, combining declared capability with recorded verification.

	Capability wins outright: a method the hardware cannot do, or that nobody has written, has
	nothing to verify. Otherwise the strongest matching record is used.

	Args:
		driver_cls (type): A Driver subclass.
		name (str): Method name.
		model (str): Optional - restrict to records for this instrument model. Useful because one
			driver covers a series (a DS1052E and a DS1054Z share RigolDS1000Z) and a command can
			work on one and not another.

	Returns:
		tuple: (VerificationStatus, record_or_reason). The second element is the decorator's
			reason string for a derived status, the winning record dict for a recorded one, or
			None when nothing is known.
	'''

	status, reason = declared_capability(driver_cls, name)
	if status is not None:
		return status, reason

	records = load_verification_records(driver_cls).get(name, [])

	best = None
	best_strength = -1
	failures = []

	for record in records:

		if model is not None and record.get("model") not in (None, model):
			continue

		try:
			record_status = VerificationStatus(record.get("status", "unverified"))
		except ValueError:
			record_status = VerificationStatus.UNVERIFIED

		if record_status == VerificationStatus.FAILED:
			failures.append(record)
			continue

		strength = _STATUS_STRENGTH.get(record_status, 0)
		if strength > best_strength:
			best, best_strength = record, strength

	if best is not None:
		return VerificationStatus(best.get("status", "unverified")), best

	# Only failures on file. Report the failure rather than the absence - "we tried and it broke"
	# is far more useful than "nothing is known".
	if failures:
		return VerificationStatus.FAILED, failures[0]

	return VerificationStatus.UNVERIFIED, None

def capability_report(driver, model:str=None) -> dict:
	''' Every category-API method of a driver, with its status.

	This is the single call a GUI or a coverage table wants: it merges what the decorators declare
	with what the records show, so no caller has to know that capability and verification are
	stored in different places.

	Args:
		driver (Driver|type): A Driver instance or class.
		model (str): Optional instrument model to restrict records to.

	Returns:
		dict: {method_name: {"status": VerificationStatus, "detail": reason/record}}
	'''

	driver_cls = driver if inspect.isclass(driver) else type(driver)

	report = {}
	for name in sorted(category_api_methods(driver_cls)):
		status, detail = method_status(driver_cls, name, model=model)
		report[name] = {"status": status, "detail": detail}

	return report

def summarize_report(report:dict) -> dict:
	''' Counts each status in a capability_report(), for a coverage table.

	Args:
		report (dict): Output of capability_report().

	Returns:
		dict: {VerificationStatus: count}, including zero counts, so a table has stable columns.
	'''

	counts = {status: 0 for status in VerificationStatus}

	for entry in report.values():
		counts[entry["status"]] += 1

	return counts
