""" The DataAcquisition category, against a stub driver.

Core ships no DAQ driver (they live in satellite packages such as constellation-ni, to keep vendor
dependencies out of core), so the category is exercised here through the smallest possible
concrete subclass. Covers the two ways a DAQ differs from the SCPI categories: settings are stored
rather than read back, and hardware calls go through `_run_hw()` rather than Driver.query().
"""

import numpy as np
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

	def _hw_start_stream(self, specs, sample_rate, chunk_samples, clock_source):
		self._maybe_fail()
		self.stream_specs = specs
		self.stream_pos = 0
		self.stream_stopped = False
		return 1.0 / sample_rate

	def _hw_read_stream(self, n_samples, timeout_s):
		self._maybe_fail()
		# A ramp across the whole stream, so contiguity between chunks is visible.
		block = [float(self.stream_pos + k) for k in range(n_samples)]
		self.stream_pos += n_samples
		return {s["channel"]: list(block) for s in self.stream_specs}

	def _hw_stop_stream(self):
		self.stream_stopped = True

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

# --- Streaming ---------------------------------------------------------------------------------

def test_stream_chunks_are_contiguous():
	daq = _StubDAQ()
	daq.set_sample_rate(1000.0)
	daq.set_ai_channel(1, -10.0, 10.0, DataAcquisition.TERM_RSE)
	assert daq.start_stream(channels=[1], chunk_samples=4)
	assert daq.state.streaming and daq.is_streaming()

	a, b = daq.read_stream(), daq.read_stream()
	assert (a["first_sample"], b["first_sample"]) == (0, 4)
	assert isinstance(a["channels"][1], np.ndarray) and a["channels"][1].dtype == np.float64
	assert np.concatenate([a["channels"][1], b["channels"][1]]).tolist() == [float(k) for k in range(8)]
	assert b["t_s"][0] == a["t_s"][-1] + a["dt_s"]
	assert daq.state.ai_channels[1].last_value_V == 7.0

	assert daq.stop_stream() == 8
	assert daq.stream_stopped and not daq.state.streaming

def test_snapshots_are_refused_while_streaming_but_digital_io_works():
	daq = _StubDAQ()
	daq.set_ai_channel(0, -10.0, 10.0, DataAcquisition.TERM_RSE)
	daq.start_stream(channels=[0], chunk_samples=2)
	assert daq.read_ai_single(channels=[0]) == {}
	assert daq.acquire(channels=[0]) == {}
	assert daq.start_stream(channels=[0]) is False
	assert daq.write_digital_line(0, True) is True
	daq.refresh_data()                                   # a GUI poll must not touch the stream
	assert daq.read_stream()["first_sample"] == 0
	daq.stop_stream()
	assert daq.read_ai_single(channels=[0]) == {0: 0.0}

def test_a_failed_stream_read_stops_the_stream():
	daq = _StubDAQ()
	daq.start_stream(channels=[0], chunk_samples=2)
	daq.fail_with = ValueError("buffer overflow")
	assert daq.read_stream() == {}
	assert not daq.is_streaming() and daq.stream_stopped
	assert daq.online
	assert daq.last_stream_end == {"reason": "read failed (instrument error)", "samples": 0}

def test_stream_loop_reports_samples_recorded_before_a_failed_read():
	daq = _StubDAQ()
	reads = []

	def fail_on_third(chunk):
		reads.append(chunk)
		if len(reads) == 2:
			daq.fail_with = ValueError("buffer overflow")

	assert daq.stream(fail_on_third, channels=[0], chunk_samples=5) == 10
	assert daq.last_stream_end["reason"].startswith("read failed")

def test_stream_loop_stops_on_each_condition():
	daq = _StubDAQ()
	daq.set_sample_rate(1000.0)
	daq.set_ai_channel(0, -10.0, 10.0, DataAcquisition.TERM_RSE)
	seen = []

	assert daq.stream(seen.append, max_chunks=3, channels=[0], chunk_samples=10) == 30
	assert len(seen) == 3 and not daq.is_streaming()
	assert daq.last_stream_end == {"reason": "max_chunks=3 reached", "samples": 30}

	# 25 ms of signal at 1 kHz in 10-sample chunks: stops after the chunk that crosses it.
	assert daq.stream(lambda c: None, duration_s=0.025, channels=[0], chunk_samples=10) == 30
	assert daq.last_stream_end["reason"] == "duration_s=0.025 reached"

	assert daq.stream(lambda c: c["first_sample"] < 20 or False, channels=[0], chunk_samples=10) == 30
	assert daq.last_stream_end["reason"] == "callback returned False"

def test_stream_is_stopped_when_the_callback_raises():
	daq = _StubDAQ()

	def boom(chunk):
		raise RuntimeError("consumer crashed")

	try:
		daq.stream(boom, channels=[0], chunk_samples=2)
	except RuntimeError:
		pass
	assert not daq.is_streaming() and daq.stream_stopped

def test_close_stops_a_running_stream():
	daq = _StubDAQ()
	daq.start_stream(channels=[0], chunk_samples=2)
	daq.close()
	assert daq.stream_stopped and not daq.is_streaming()
	assert daq.last_stream_end["reason"] == "driver closed"

def test_stream_info_describes_the_running_stream():
	daq = _StubDAQ()
	assert daq.stream_info() is None
	daq.set_sample_rate(500.0)
	daq.set_ai_channel(2, -1.0, 1.0, DataAcquisition.TERM_NRSE)
	daq.start_stream(channels=[2], chunk_samples=8)
	daq.read_stream()
	info = daq.stream_info()
	assert info["sample_rate_Hz"] == 500.0 and info["samples_read"] == 8 and info["chunk_samples"] == 8
	assert info["channels"] == {2: {"terminal_config": "nrse", "range_min_V": -1.0, "range_max_V": 1.0}}
	assert info["started_at"].tzinfo is not None

def test_dummy_stream_is_contiguous_and_paced():
	import time
	daq = _StubDAQ(dummy=True)
	daq.set_sample_rate(2000.0)
	t0 = time.perf_counter()
	chunks = []
	daq.stream(chunks.append, max_chunks=5, chunk_samples=40)      # 5 x 20 ms of signal
	assert time.perf_counter() - t0 >= 0.09
	assert [c["first_sample"] for c in chunks] == [0, 40, 80, 120, 160]
	assert set(chunks[0]["channels"]) == {0, 1}
