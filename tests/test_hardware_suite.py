""" The hardware suite's own bookkeeping, checked without an instrument.

`tests/hardware/` can only be *run* at a bench, but the logic deciding what a run concludes -
which is the part that ends up written into verification.yaml and believed later - must not be
verifiable only by someone holding a scope. Everything here exercises that logic against a dummy
driver.

The suite's harness can also be smoke-tested end to end with no instrument attached:

	pytest tests/hardware --dummy --driver=RigolDS1000Z
	pytest tests/hardware --dummy --driver=RigolDS1000Z --confirm

`--dummy` cannot write records, by construction: a dummy instrument is not evidence of anything,
and a fabricated record is worse than no record at all.
"""

import os
import sys

import pytest
import yaml

import pylogfile.base as plf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "hardware"))

from hardware_support import Recorder, already_confirmed
from constellation.verification import VerificationStatus
import constellation.verification_writer as vw
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000E_dvr import RigolDS1000E

IDN = "RIGOL TECHNOLOGIES,DS1054Z,DS1ZA000000001,00.04.04"

class FakeConfig:
	''' Stands in for pytest's Config - the Recorder only ever asks it for three options. '''

	def __init__(self, **options):
		self.options = {"--model": None, "--operator": "tester"} | options

	def getoption(self, name):
		return self.options.get(name)

@pytest.fixture
def scope():

	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL

	instrument = RigolDS1000Z("DUMMY", log=log, dummy=True)
	instrument.id.idn_model = IDN

	return instrument

@pytest.fixture
def recorder(scope):

	return Recorder(scope, FakeConfig())

def test_model_is_taken_from_the_idn(recorder):
	""" The model is what records are keyed by - one driver covers a series, and a command can
	work on a DS1054Z and not on a DS1052E. """

	assert recorder.model == "DS1054Z"
	assert recorder.idn == IDN

def test_model_survives_a_malformed_idn(scope):
	""" An instrument that answers *IDN? with something unexpected must not crash the run - it
	just means the model can't be pinned down. """

	scope.id.idn_model = "some-instrument"

	assert Recorder(scope, FakeConfig()).model is None

def test_an_explicit_model_option_wins(scope):

	assert Recorder(scope, FakeConfig(**{"--model": "DS1104Z"})).model == "DS1104Z"

@pytest.mark.parametrize("order", [
	[VerificationStatus.ROUNDTRIP, VerificationStatus.FAILED],
	[VerificationStatus.FAILED, VerificationStatus.ROUNDTRIP],
], ids=["fail-last", "fail-first"])
def test_a_failure_anywhere_in_a_run_is_the_runs_result(order, recorder):
	""" A method is exercised at several values. If any of them broke it, the method is broken -
	regardless of which order the passes and failures arrived in. """

	for status in order:
		recorder.record("set_div_volt", status)

	assert recorder.results["set_div_volt"][0] == VerificationStatus.FAILED

def test_a_roundtrip_does_not_overwrite_a_confirmation_within_a_run(recorder):
	""" Same rule as the file-level merge, applied inside a single session: several checks touch
	the same method, and the human-confirmed one is the strongest thing learned. """

	recorder.record("set_div_volt", VerificationStatus.CONFIRMED)
	recorder.record("set_div_volt", VerificationStatus.ROUNDTRIP)

	assert recorder.results["set_div_volt"][0] == VerificationStatus.CONFIRMED

def test_both_halves_of_a_set_get_pair_are_recorded(recorder):
	""" Neither half can be verified alone: the getter is the only thing reading the setter back,
	and the setter is the only thing giving the getter something to read. """

	recorder.record(("set_div_volt", "get_div_volt"), VerificationStatus.ROUNDTRIP)

	assert set(recorder.results) == {"set_div_volt", "get_div_volt"}

def test_a_declared_method_is_never_recorded():
	""" A method the hardware cannot do has nothing to verify. Even if a test somehow reached it,
	the recorder must not create a record that contradicts the decorator. """

	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL

	instrument = RigolDS1000E("DUMMY", log=log, dummy=True)
	rec = Recorder(instrument, FakeConfig())

	rec.record("set_div_time", VerificationStatus.ROUNDTRIP)

	assert rec.results == {}

def test_flush_writes_what_the_run_learned(recorder, tmp_path, monkeypatch):
	""" End to end: a run's conclusions reach the file, with provenance attached. """

	path = tmp_path / "verification.yaml"
	path.write_text("# header\n\n", encoding="utf-8")
	monkeypatch.setattr(vw, "verification_file_for", lambda cls: str(path))

	recorder.record(("set_div_volt", "get_div_volt"), VerificationStatus.CONFIRMED)
	recorder.record("set_trigger_level", VerificationStatus.FAILED, note="read back None")

	actions = recorder.flush()

	assert set(actions) == {"set_div_volt", "get_div_volt", "set_trigger_level"}

	document = yaml.safe_load(path.read_text(encoding="utf-8"))["RigolDS1000Z"]

	assert document["set_div_volt"][0]["status"] == "confirmed"
	assert document["set_div_volt"][0]["idn"] == IDN
	assert document["set_div_volt"][0]["by"] == "tester"
	assert document["set_trigger_level"][0]["status"] == "failed"
	assert document["set_trigger_level"][0]["note"] == "read back None"

def test_a_confirmed_method_is_recognised_as_already_done(recorder, tmp_path, monkeypatch):
	""" What makes a moderated run resumable: it skips what a person already confirmed, so an
	interrupted session doesn't have to be re-answered from the top. """

	import constellation.verification as cv

	path = tmp_path / "verification.yaml"
	path.write_text("# header\n\n", encoding="utf-8")
	monkeypatch.setattr(vw, "verification_file_for", lambda cls: str(path))
	monkeypatch.setattr(cv, "verification_file_for", lambda cls: str(path))

	recorder.record("set_div_volt", VerificationStatus.CONFIRMED)
	recorder.flush()

	assert already_confirmed(RigolDS1000Z, ["set_div_volt"], model="DS1054Z", idn=IDN)

	# ...but not on a different firmware, and not for a method nobody has confirmed.
	assert not already_confirmed(RigolDS1000Z, ["set_div_volt"], model="DS1054Z", idn="RIGOL TECHNOLOGIES,DS1054Z,DS1ZA000000001,99.99.99")
	assert not already_confirmed(RigolDS1000Z, ["set_trigger_level"], model="DS1054Z", idn=IDN)
