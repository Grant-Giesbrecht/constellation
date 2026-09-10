""" Fixtures for the hardware suite: the instrument under test, the human-confirmation prompt, and
the recorder that stamps verification.yaml at the end of a run.

Nothing here is oscilloscope-specific. Because drivers are written against a category API, one
parametrized test module covers every driver in a category, and these fixtures are what the next
category's module will reuse unchanged.

See docs/hardware_verification.md.
"""

import pytest

import pylogfile.base as plf

from hardware_support import Recorder

# Drivers the suite knows how to construct, by class name. A plain table rather than a scan of the
# package: importing every driver to find one would drag in every optional vendor dependency, and
# `--driver` is meant to be typed by a human who knows what is on the bench.
def driver_registry() -> dict:

	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z
	from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000E_dvr import RigolDS1000E
	from constellation.instrument_control.arb_waveform_generator.drivers.Siglent_SDG2000X_dvr import SiglentSDG2000X

	return {cls.__name__: cls for cls in (RigolDS1000Z, RigolDS1000E, SiglentSDG2000X)}

@pytest.fixture(scope="session")
def hw_log():

	log = plf.LogPile()
	log.terminal_level = plf.WARNING

	return log

@pytest.fixture(scope="session")
def instrument(request, hw_log):
	""" The connected driver, built from --driver and --address.

	Session-scoped: connecting is slow, and a moderated run wants one continuous session in front
	of one instrument rather than a reconnect per test.
	"""

	address = request.config.getoption("--address")
	driver_name = request.config.getoption("--driver")
	dummy = request.config.getoption("--dummy")

	if not driver_name:
		pytest.skip("--driver is required alongside --address")

	registry = driver_registry()
	if driver_name not in registry:
		pytest.skip(f"unknown driver >{driver_name}<. Known: {sorted(registry)}")

	driver = registry[driver_name](address or "DUMMY", log=hw_log, dummy=dummy)

	if not driver.online:
		pytest.skip(f"could not connect to >{address}< as {driver_name}")

	# The instrument is someone's bench setup, not a scratch device. Snapshot what it was doing so
	# the run can put it back - a test suite that leaves the timebase somewhere random is a test
	# suite people stop running.
	driver.refresh_state()
	try:
		entry_state = driver.state_to_dict()
	except Exception as e:
		hw_log.warning(f"Could not snapshot the instrument's entry state - it will be left as the tests leave it. ({e})")
		entry_state = None

	yield driver

	if entry_state is not None:
		try:
			driver.load_state_dict(entry_state)
			driver.apply_state()
		except Exception as e:
			hw_log.warning(f"Could not restore the instrument's entry state. ({e})")

	driver.close()

@pytest.fixture(scope="session")
def channel(request, instrument):
	""" The channel the suite exercises. One channel is enough: a per-channel bug is a driver
	indexing bug, which the dummy-mode suite already covers without tying up hardware. """

	return request.config.getoption("--channel") or instrument.first_channel

@pytest.fixture(scope="session")
def moderated(request) -> bool:

	return bool(request.config.getoption("--confirm"))

@pytest.fixture(scope="session")
def recorder(request, instrument):

	rec = Recorder(instrument, request.config)

	yield rec

	# A dummy instrument confirms nothing about hardware. Writing records from one would be
	# fabricating evidence, which is worse than having none - so the mode cannot write, and the
	# check lives here rather than relying on the operator remembering --no-record.
	if request.config.getoption("--dummy"):
		print(f"\n--dummy: {len(rec.results)} result(s) discarded. A dummy instrument is not evidence; verification.yaml untouched.")
		return

	if request.config.getoption("--no-record"):
		print(f"\n--no-record: {len(rec.results)} result(s) discarded, verification.yaml untouched.")
		return

	actions = rec.flush()
	changed = {name: action for name, action in actions.items() if action != "kept"}
	print(f"\nverification.yaml: {len(changed)} record(s) written, {len(actions) - len(changed)} left as-is (existing evidence was stronger).")

@pytest.fixture
def confirm(request, moderated):
	""" Asks a human whether the instrument physically did the right thing.

	This is the whole reason moderated mode exists. A round trip only proves a set/get pair agrees
	with itself: if a driver's setter writes the timebase and its getter reads the timebase, then
	setting volts/div round-trips perfectly while the driver drives the wrong control. Only
	someone looking at the front panel catches that.

	Returns a callable(prompt) -> True / False / None, where None means "not in moderated mode, or
	the operator skipped this one" - never a silent True. Callers must not treat None as success.
	"""

	if not moderated:
		return lambda prompt: None

	capman = request.config.pluginmanager.getplugin("capturemanager")

	def _confirm(prompt:str):

		# pytest swallows stdout AND stdin by default, so a prompt written here would never appear
		# and input() would raise "reading from stdin while output is captured". Capture has to be
		# suspended with in_=True: global_and_fixture_disabled() alone restores stdout but leaves
		# stdin as pytest's DontReadFromInput, which is precisely the half a prompt needs.
		capman.suspend_fixture()
		capman.suspend_global_capture(in_=True)

		try:
			print("\n" + "-" * 78)
			print(prompt)
			answer = input("Did the instrument do this? [y]es / [n]o / [s]kip: ").strip().lower()
			print("-" * 78)
		finally:
			capman.resume_global_capture()
			capman.resume_fixture()

		if answer.startswith("y"):
			return True
		if answer.startswith("n"):
			return False

		return None

	return _confirm
