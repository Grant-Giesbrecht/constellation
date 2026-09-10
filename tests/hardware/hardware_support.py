""" Helpers shared by the hardware suite.

A plain module rather than more fixtures in conftest.py: these are called from inside test bodies,
and `import conftest` is fragile (pytest gives conftest modules special, version-dependent import
handling, and there are two of them in this tree).
"""

import pytest

from constellation.verification import VerificationStatus, method_status, declared_capability
from constellation.verification_writer import build_record, update_records

def already_confirmed(driver_cls, names, model:str=None, idn:str=None) -> bool:
	''' Whether every method in `names` already holds a valid (non-stale) confirmed record.

	Used to make moderated runs resumable: they are slow and human-attended, so re-confirming
	fifty methods to reach the one that changed is how a run gets abandoned halfway. `--recheck`
	overrides.
	'''

	for name in names:

		status, _ = method_status(driver_cls, name, model=model, idn=idn)
		if status != VerificationStatus.CONFIRMED:
			return False

	return True

def skip_if_unavailable(driver_cls, names) -> None:
	''' Skips a check whose methods the driver declares it cannot perform.

	The decorator is the authority: a DS1000E cannot set its timebase over SCPI, so there is
	nothing to test and nothing to record. `verification.yaml` must not restate it.
	'''

	for name in names:

		declared, reason = declared_capability(driver_cls, name)
		if declared is not None:
			pytest.skip(f"{driver_cls.__name__}.{name}() is {declared.value}: {reason}")

class Recorder:
	''' Collects one status per method over a run and writes them all at the end.

	Batched rather than written per-test on purpose: a run interrupted halfway through should not
	leave a half-rewritten verification.yaml, and rewriting the file thirty times during a session
	makes the file's mtime meaningless.
	'''

	def __init__(self, driver, config):

		self.driver = driver
		self.driver_cls = type(driver)
		self.config = config
		self.results = {}

		self.idn = (getattr(driver.id, "idn_model", "") or "").strip() or None
		self.model = config.getoption("--model") or self._model_from_idn(self.idn)
		self.operator = config.getoption("--operator")

	@staticmethod
	def _model_from_idn(idn:str):
		''' Pulls the model field out of a SCPI *IDN? response ("<vendor>,<model>,<serial>,<fw>"). '''

		if not idn:
			return None

		parts = [p.strip() for p in idn.split(",")]

		return parts[1] if len(parts) > 1 else None

	def record(self, names, status:VerificationStatus, note:str=None) -> None:
		''' Notes a result for one or more methods.

		A check exercises a set/get pair together - neither half can be verified without the
		other - so both names get the record, with the same provenance.

		A stronger result within one run wins (a method confirmed by a human should not be
		overwritten by a later round-trip touching the same method), except that a failure always
		wins: if any part of a run showed the method broken, that is the run's result.
		'''

		if isinstance(names, str):
			names = [names]

		for name in names:

			if declared_capability(self.driver_cls, name)[0] is not None:
				continue

			previous = self.results.get(name)
			if previous is not None:
				if previous[0] == VerificationStatus.FAILED:
					continue
				if status != VerificationStatus.FAILED and _rank(previous[0]) >= _rank(status):
					continue

			self.results[name] = (status, note)

	def flush(self) -> dict:
		''' Merges everything collected into verification.yaml. Returns {method: action}. '''

		if not self.results:
			return {}

		records = {name: build_record(self.driver_cls, name, status, model=self.model, idn=self.idn,
			by=self.operator, note=note) for name, (status, note) in self.results.items()}

		return update_records(self.driver_cls, records)

def _rank(status:VerificationStatus) -> int:

	return {VerificationStatus.UNVERIFIED: 0, VerificationStatus.ROUNDTRIP: 1,
		VerificationStatus.CONFIRMED: 2}.get(status, -1)

# --- The check table's runner ------------------------------------------------------------------
#
# Category-agnostic, and here rather than in a test module because both category modules need it:
# duplicating the runner per category is how two copies of "what counts as verified" drift apart,
# and that logic is precisely what ends up written into verification.yaml and believed later.

def close_enough(expected, actual, rel:float=0.02, abs_tol:float=0.0) -> bool:
	''' Numeric comparison with room for the instrument's own quantization.

	An instrument does not necessarily store what you send it: a scope's volts/div and time/div
	snap to a 1-2-5 sequence, and offsets quantize to a fraction of a division. Values in a check
	table should sit on that grid so the tolerance can stay tight - a loose tolerance here would
	pass a driver that is off by a factor of two.
	'''

	if expected is None or actual is None:
		return False

	return abs(float(actual) - float(expected)) <= max(abs_tol, abs(float(expected)) * rel)

class Check:
	''' One set/get pair, the values to drive it with, and what a human should see.

	`methods` lists both halves because neither can be verified without the other: the getter is
	the only thing reading the setter back, and the setter is the only thing giving the getter
	something to read. A result therefore applies to both.

	`setup` optionally puts the instrument into a state where the parameter under test exists at
	all - an AWG has no frequency to read while it is generating noise. It runs once, before the
	values, and is skipped if the driver declares it can't do it.
	'''

	def __init__(self, methods, values, apply, read, prompt, matches=None, setup=None):

		self.methods = tuple(methods)
		self.values = tuple(values)
		self.apply = apply
		self.read = read
		self.prompt = prompt
		self.matches = matches or (lambda expected, actual: close_enough(expected, actual))
		self.setup = setup

	@property
	def id(self) -> str:
		return self.methods[0]

def run_check(check, instrument, channel, recorder, confirm, moderated, recheck) -> None:
	''' Drives one check and records the outcome for both halves of its set/get pair. '''

	driver_cls = type(instrument)

	skip_if_unavailable(driver_cls, check.methods)

	# Moderated runs are slow and human-attended. Re-confirming everything to reach the one method
	# that changed is how a run gets abandoned halfway through, so already-confirmed methods are
	# skipped by default. Staleness is honoured: a confirmed record whose code has since changed
	# no longer counts as confirmed, so it gets asked about again.
	if moderated and not recheck and already_confirmed(driver_cls, check.methods, model=recorder.model, idn=recorder.idn):
		pytest.skip(f"already confirmed on {recorder.model or 'this model'} - pass --recheck to re-run")

	if check.setup is not None:
		check.setup(instrument, channel)

	last_value = None

	for value in check.values:

		try:
			check.apply(instrument, channel, value)
			readback = check.read(instrument, channel)
		except Exception as e:
			recorder.record(check.methods, VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
			raise

		if not check.matches(value, readback):
			recorder.record(check.methods, VerificationStatus.FAILED, note=f"set {value!r}, read back {readback!r}")
			pytest.fail(f"{check.id}: set {value!r} but read back {readback!r}")

		last_value = value

	# The instrument is still sitting at the last value, which is what the operator is being asked
	# to look at. Asking before restoring anything is the point.
	answer = confirm(check.prompt(instrument, channel, last_value))

	if answer is False:
		recorder.record(check.methods, VerificationStatus.FAILED, note="operator reported the instrument did not do this")
		pytest.fail(f"{check.id}: operator reported the instrument did not do what was asked")

	# `None` means unmoderated, or the operator skipped - never a silent upgrade to confirmed.
	recorder.record(check.methods, VerificationStatus.CONFIRMED if answer else VerificationStatus.ROUNDTRIP)

def requires_category(instrument, category_cls) -> None:
	''' Skips a check whose category the connected instrument does not belong to.

	`pytest tests/hardware` collects every category module, but a run has exactly one instrument on
	the bench. Without this, pointing the suite at a signal generator runs the oscilloscope module
	against it and reports a dozen failures about an instrument that was never claimed to be a
	scope. Each category module guards itself with an autouse fixture calling this.
	'''

	if not isinstance(instrument, category_cls):
		pytest.skip(f"{type(instrument).__name__} is not a {category_cls.__name__} - this module verifies the {category_cls.__name__} category")
