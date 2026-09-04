""" Shared pytest configuration.

Exists for one reason: the hardware suite (`tests/hardware/`) needs command-line options, and
pytest parses options before it descends into subdirectories - so `pytest_addoption` has to live
at the top of the test tree or `--address` is rejected as an unknown argument. The fixtures that
use these options stay in `tests/hardware/conftest.py`, next to the tests that need them.

Hardware tests are skipped unless `--address` is given, so `pytest tests/` on a laptop with no
instruments on the bench behaves exactly as it did before they existed.

See docs/hardware_verification.md.
"""

import pytest

def pytest_addoption(parser):

	group = parser.getgroup("constellation hardware")

	group.addoption("--address", action="store", default=None,
		help="VISA resource string (or labmesh relay id) of the instrument to test against. Hardware tests are skipped without it.")
	group.addoption("--driver", action="store", default=None,
		help="Driver class name to instantiate, e.g. RigolDS1000Z. Required alongside --address.")
	group.addoption("--confirm", action="store_true", default=False,
		help="Moderated mode: pause after each step so a human can confirm the instrument actually did the right thing. Slower, and the only way to earn a 'confirmed' record.")
	group.addoption("--channel", action="store", type=int, default=None,
		help="Channel to exercise. Defaults to the driver's first channel.")
	group.addoption("--operator", action="store", default=None,
		help="Name recorded as `by` on confirmed records. Defaults to $USER.")
	group.addoption("--model", action="store", default=None,
		help="Instrument model recorded with each record. Defaults to the model field of the instrument's *IDN? response.")
	group.addoption("--no-record", action="store_true", default=False,
		help="Run the hardware checks but do not write verification.yaml. Use for a dry run.")
	group.addoption("--dummy", action="store_true", default=False,
		help="Smoke-test the hardware suite against a dummy driver, with no instrument attached. Records are never written in this mode - a dummy instrument is not evidence of anything. Use it to check the harness itself, not the driver.")
	group.addoption("--recheck", action="store_true", default=False,
		help="In moderated mode, re-run methods that are already confirmed instead of skipping them.")

def pytest_configure(config):

	config.addinivalue_line("markers",
		"hardware: requires a physical instrument. Skipped unless --address is given.")

def pytest_collection_modifyitems(config, items):

	if config.getoption("--address") or config.getoption("--dummy"):
		return

	skip = pytest.mark.skip(reason="needs a physical instrument - pass --address and --driver")

	for item in items:
		if "hardware" in item.keywords:
			item.add_marker(skip)
