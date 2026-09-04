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
