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

import ast
import hashlib
import inspect
import os
import textwrap
from enum import Enum

import pylogfile.base as plf

from constellation.base import Driver

VERIFICATION_FILENAME = "verification.yaml"

# Bump this when a change to the shared instrument-I/O path could plausibly change what a driver
# actually does to hardware - the relay layer, superreturn, modify_state, Driver.write/read/query.
# Every record stamped with an older epoch is then reported as stale-framework, because the
# evidence was gathered under different plumbing.
#
# Bumping is a HUMAN judgement, deliberately. "Did this refactor change instrument behaviour" is
# not machine-decidable, and auto-invalidating every record on any base.py edit would produce a
# wall of false staleness on routine refactors - which is how these systems die: people stop
# reading the warnings and re-stamp without looking.
#
# What IS automated is noticing that you touched the plumbing: FRAMEWORK_CRITICAL_FUNCTIONS below
# is hashed by a test that fails if it changed without an epoch bump. See
# tests/test_verification_records.py and docs/hardware_verification.md.
VERIFICATION_EPOCH = 1

# The functions every SCPI call passes through. Deliberately a short, explicit list rather than a
# call graph: the point is to catch changes to the shared path, not to track every dependency.
FRAMEWORK_CRITICAL_FUNCTIONS = (
	("constellation.base", "Driver.write"),
	("constellation.base", "Driver.read"),
	("constellation.base", "Driver.query"),
	("constellation.base", "Driver.query_binary"),
	("constellation.base", "Driver.write_binary"),
	("constellation.base", "Driver._relay_attempt"),
	("constellation.base", "Driver._ensure_online"),
	("constellation.base", "Driver.modify_state"),
	("constellation.base", "superreturn.__call__"),
	("constellation.relay", "DirectSCPIRelay.write"),
	("constellation.relay", "DirectSCPIRelay.read"),
	("constellation.relay", "DirectSCPIRelay.query"),
)

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
	
	# Derived at report time by comparing a record against the code and instrument in front of
	# you. A record does not become wrong when something changes - it becomes evidence about a
	# situation that no longer holds, which is a different thing and worth saying differently.
	STALE_CODE = "stale-code"
	STALE_FRAMEWORK = "stale-framework"
	STALE_FIRMWARE = "stale-firmware"

# Every way a record can have expired. All of them are reported to a user as "not verified" - the
# detail says why, but the headline never claims verification. Fail closed.
STALE_STATUSES = {
	VerificationStatus.STALE_CODE,
	VerificationStatus.STALE_FRAMEWORK,
	VerificationStatus.STALE_FIRMWARE,
}

# Statuses that assert the driver actually worked. Only these are subject to staleness checks -
# there is nothing to invalidate about "unverified", and a failure does not become less of a
# failure because the code changed.
VERIFIED_STATUSES = {
	VerificationStatus.ROUNDTRIP,
	VerificationStatus.CONFIRMED,
}

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
	VerificationStatus.STALE_CODE,
	VerificationStatus.STALE_FRAMEWORK,
	VerificationStatus.STALE_FIRMWARE,
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

def _normalized_source_hash(func) -> str:
	''' A hash of what a function *does*, insensitive to how it is written.

	The source is parsed to an AST and its decorators and docstring are stripped before hashing,
	so reformatting, a comment, or a docstring fix does not invalidate hardware evidence - while a
	changed SCPI string or a changed calculation does. Hashing raw text instead would produce
	constant false staleness and train people to ignore it.

	Args:
		func (callable): The function to hash.

	Returns:
		str: 16 hex characters, or "" if the source could not be read (a C function, an
			interactively-defined class, a stripped install).
	'''

	try:
		source = textwrap.dedent(inspect.getsource(func))
		tree = ast.parse(source)
	except (OSError, TypeError, SyntaxError, IndentationError):
		return ""

	node = tree.body[0] if tree.body else None
	if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):

		# Decorators are policy applied to the body, not the body itself. @superreturn coming or
		# going is a real change, but it is caught by the framework epoch rather than by every
		# method's hash flipping at once.
		node.decorator_list = []

		# The name is not part of what the function does, and it is already the record's key - a
		# rename is caught as an orphaned record, not as staleness. Normalizing it out also keeps
		# the hash comparable between two functions that differ only in name.
		node.name = ""

		if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
			node.body = node.body[1:]

	return hashlib.sha256(ast.dump(tree).encode("utf-8")).hexdigest()[:16]

def method_code_hash(driver_cls, name:str) -> str:
	''' Hash of one driver method's implementation, for detecting that hardware evidence no
	longer describes the code that is there now.

	Per-method rather than per-commit on purpose. A commit hash says *when* the record was made,
	not *whether the relevant code changed* - it would invalidate every record in the project on
	any commit, needs git to interpret, and is meaningless in an installed wheel with no .git.

	Args:
		driver_cls (type): A Driver subclass.
		name (str): Method name.

	Returns:
		str: 16 hex characters, or "" if unavailable.
	'''

	raw = inspect.getattr_static(driver_cls, name, None)
	if raw is None:
		return ""

	# Unwrap the superreturn descriptor to reach the driver's own function - the same unwrap
	# Oscilloscope._get_waveform_accepts_batch_hint() does.
	func = getattr(raw, "func", raw)
	func = getattr(func, "__func__", func)

	return _normalized_source_hash(func)

def _resolve_dotted(module_name:str, dotted:str):
	''' Resolves "Class.method" (or a bare function name) inside an imported module. '''

	import importlib

	obj = importlib.import_module(module_name)
	for part in dotted.split("."):
		obj = inspect.getattr_static(obj, part) if inspect.isclass(obj) else getattr(obj, part)

	return getattr(obj, "func", obj)

def framework_fingerprint() -> str:
	''' A single hash over every function on the shared instrument-I/O path.

	Used by a test to detect that the plumbing changed without VERIFICATION_EPOCH being bumped.
	It does not invalidate anything by itself - a human decides whether a plumbing change
	invalidates hardware evidence, and records that decision by bumping the epoch.

	Returns:
		str: 16 hex characters.
	'''

	parts = []
	for module_name, dotted in FRAMEWORK_CRITICAL_FUNCTIONS:
		try:
			parts.append(f"{module_name}.{dotted}={_normalized_source_hash(_resolve_dotted(module_name, dotted))}")
		except Exception:
			parts.append(f"{module_name}.{dotted}=<unresolved>")

	return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:16]

def record_staleness(driver_cls, name:str, record:dict, idn:str=None):
	''' Decides whether one verified record still describes the situation in front of you.

	Checked in order of specificity - the most precise explanation wins, so a user is told the
	most actionable thing rather than the broadest.

	Args:
		driver_cls (type): A Driver subclass.
		name (str): Method name the record belongs to.
		record (dict): The record.
		idn (str): Optional live `*IDN?` string from the connected instrument. Supplying it
			enables the firmware check; without it, firmware staleness cannot be assessed and is
			not guessed at.

	Returns:
		VerificationStatus: One of the STALE_* values, or None if the record still stands.
	'''
	
	try:
		status = VerificationStatus(record.get("status"))
	except ValueError:
		return None

	# Only claims of success can go stale. "unverified" has nothing to invalidate, and a failure
	# does not become less of a failure because the code moved on.
	if status not in VERIFIED_STATUSES:
		return None

	# 1. The method's own code changed. Most precise, and most likely to be actionable.
	recorded_hash = record.get("code_hash")
	if recorded_hash:
		current = method_code_hash(driver_cls, name)
		if current and current != recorded_hash:
			return VerificationStatus.STALE_CODE

	# 2. The shared I/O path changed enough that someone bumped the epoch.
	recorded_epoch = record.get("epoch")
	if recorded_epoch is not None and recorded_epoch < VERIFICATION_EPOCH:
		return VerificationStatus.STALE_FRAMEWORK

	# 3. The instrument in front of you is not the one the record was made against. Only
	#    assessable when a live IDN is supplied - absence of information is not evidence of
	#    staleness, and guessing here would cry wolf on every offline report.
	recorded_idn = record.get("idn")
	if idn and recorded_idn and idn.strip() != recorded_idn.strip():
		return VerificationStatus.STALE_FIRMWARE

	return None

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

def method_status(driver_cls, name:str, model:str=None, idn:str=None) -> tuple:
	''' Resolves one method's status, combining declared capability with recorded verification.

	Capability wins outright: a method the hardware cannot do, or that nobody has written, has
	nothing to verify. Otherwise the strongest matching record is used.

	Args:
		driver_cls (type): A Driver subclass.
		name (str): Method name.
		model (str): Optional - restrict to records for this instrument model. Useful because one
			driver covers a series (a DS1052E and a DS1054Z share RigolDS1000Z) and a command can
			work on one and not another.
		idn (str): Optional live `*IDN?` string, enabling the firmware staleness check.

	Returns:
		tuple: (VerificationStatus, record_or_reason). The second element is the decorator's
			reason string for a derived status, the winning record dict for a recorded one, or
			None when nothing is known.
	
	A record that no longer describes the code or instrument in front of you is reported as one of
	the STALE_* statuses rather than as the success it claims. This is the case the whole scheme
	exists to prevent: a driver verified on hardware, then changed, then used by someone who reads
	"confirmed" and trusts it.
	'''

	status, reason = declared_capability(driver_cls, name)
	if status is not None:
		return status, reason

	records = load_verification_records(driver_cls).get(name, [])

	best = None
	best_strength = -1
	failures = []
	stale_records = []

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

		# A stale record's claim no longer stands. Keep it as the winner if nothing better exists
		# (its detail explains what expired, which beats a bare "unverified"), but report the
		# staleness rather than the claim, and rank it below anything still valid.
		stale = record_staleness(driver_cls, name, record, idn=idn)
		if stale is not None:
			stale_records.append((stale, record))
			continue

		strength = _STATUS_STRENGTH.get(record_status, 0)
		if strength > best_strength:
			best, best_strength = record, strength

	if best is not None:
		return VerificationStatus(best.get("status", "unverified")), best
	
	# Nothing valid on file, but something used to be. Say which way it expired.
	if stale_records:
		return stale_records[0]

	# Only failures on file. Report the failure rather than the absence - "we tried and it broke"
	# is far more useful than "nothing is known".
	if failures:
		return VerificationStatus.FAILED, failures[0]

	return VerificationStatus.UNVERIFIED, None

def capability_report(driver, model:str=None, idn:str=None) -> dict:
	''' Every category-API method of a driver, with its status.

	This is the single call a GUI or a coverage table wants: it merges what the decorators declare
	with what the records show, so no caller has to know that capability and verification are
	stored in different places.

	Args:
		driver (Driver|type): A Driver instance or class.
		model (str): Optional instrument model to restrict records to.
		idn (str): Optional live `*IDN?`. When `driver` is a connected instance this defaults to
			that instrument's own IDN, so a report taken against real hardware automatically
			notices records made against a different firmware.

	Returns:
		dict: {method_name: {"status": VerificationStatus, "detail": reason/record,
			"trusted": bool}}. `trusted` is the one field a UI needs: True only for a claim that
			still stands. Everything uncertain - unverified, unimplemented, stale, failed - is
			False. Fail closed.
	'''

	driver_cls = driver if inspect.isclass(driver) else type(driver)

	if idn is None and not inspect.isclass(driver):
		idn = getattr(getattr(driver, "id", None), "idn_model", None) or None

	report = {}
	for name in sorted(category_api_methods(driver_cls)):
		status, detail = method_status(driver_cls, name, model=model, idn=idn)
		report[name] = {
			"status": status,
			"detail": detail,
			"trusted": status in VERIFIED_STATUSES,
		}

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
