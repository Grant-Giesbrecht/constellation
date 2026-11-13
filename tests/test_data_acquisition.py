""" The DataAcquisition category, against a stub driver.

Core ships no DAQ driver (they live in satellite packages such as constellation-ni, to keep vendor
dependencies out of core), so the category is exercised here through the smallest possible
concrete subclass. Covers the two ways a DAQ differs from the SCPI categories: settings are stored
rather than read back, and hardware calls go through `_run_hw()` rather than Driver.query().
"""

import pylogfile.base as plf

from constellation.relay import CommandRelay, RelayErrorKind
from constellation.instrument_control.data_acquisition.data_acquisition_ctg import DataAcquisition

class _StubRelay(CommandRelay):

	def __init__(self):
		super().__init__()
		self.present = True

	def connect(self):
		return self.present

	def close(self):
		pass

	def write(self, cmd):
		return False

	def read(self):
		return False, ""

	def query(self, cmd):
		return False, ""

	def probe(self, timeout_s=1.5):
		return self.present

class _StubDAQ(DataAcquisition):

	def __init__(self, dummy=False):
		log = plf.LogPile()
		log.terminal_level = plf.CRITICAL
		self.fail_with = None
		super().__init__("stub", log, relay=_StubRelay(), expected_idn="STUB", max_ai_channels=4,
			num_digital_lines=2, dummy=dummy)

	def _maybe_fail(self):
		if self.fail_with is not None:
			raise self.fail_with

	def _hw_read_ai_single(self, specs):
		self._maybe_fail()
		return [float(s["channel"]) for s in specs]

	def _hw_acquire(self, specs, sample_rate, n_samples, mode, clock_source):
		self._maybe_fail()
		return 1.0 / sample_rate, {s["channel"]: [float(s["channel"])] * n_samples for s in specs}

	def _hw_read_digital_line(self, line):
		return True

	def _hw_write_digital_line(self, line, value):
		self._maybe_fail()

	def _hw_device_identity(self):
		return "Vendor,STUB,0001,1.0"

def test_dummy_setters_store_their_values():
	""" Regression: setters used modify_state(None, ...), which dummy mode reads as a getter, so
	every value - including init_dummy_state()'s own seeding - was silently dropped. """
	daq = _StubDAQ(dummy=True)
	assert daq.state.sample_rate_Hz == 10000.0          # seeded by init_dummy_state()
	daq.set_sample_rate(12345.0)
	daq.set_ai_channel(2, -1.0, 1.0, DataAcquisition.TERM_RSE)
	assert daq.state.sample_rate_Hz == 12345.0
	assert daq.state.ai_channels[2].terminal_config == DataAcquisition.TERM_RSE
	assert daq.state.ai_channels[2].range_max_V == 1.0

def test_hardware_setters_and_measurements_are_stored():
	daq = _StubDAQ()
	assert daq.online and daq.verified_hardware
	daq.set_samples_per_channel(16)
	daq.set_ai_channel(3, -10.0, 10.0, DataAcquisition.TERM_RSE)
	assert daq.state.samples_per_channel == 16
	assert daq.read_ai_single(channels=[3]) == {3: 3.0}
	assert daq.state.ai_channels[3].last_value_V == 3.0
	assert len(daq.acquire(channels=[3])["channels"][3]) == 16
	assert daq.state.last_acquisition["channels"]["3"][0] == 3.0

def test_a_usage_error_leaves_the_driver_online():
	daq = _StubDAQ()
	daq.fail_with = TypeError("bad argument")
	assert daq.read_ai_single(channels=[0]) == {}
	assert daq.online
	assert daq.relay.last_error_kind == RelayErrorKind.USAGE

def test_a_lost_device_takes_the_driver_offline():
	daq = _StubDAQ()
	daq.fail_with = OSError("device gone")
	daq.relay.present = False
	assert daq.acquire(channels=[0]) == {}
	assert not daq.online

def test_a_failed_write_does_not_update_state():
	daq = _StubDAQ()
	daq.fail_with = TypeError("bad argument")
	assert daq.write_digital_line(1, True) is None
	assert "1" not in daq.state.digital_out

def test_invalid_channels_and_lines_are_refused():
	daq = _StubDAQ(dummy=True)
	assert daq.set_ai_channel(4) is None                # channels 0..3
	assert daq.read_digital_line(2) is None             # lines 0..1
	assert daq.read_ai_single(channels=[9]) == {}
