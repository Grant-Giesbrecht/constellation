""" Tests for RigolDS1000Z.get_waveform().

Covers two things:
  - A real bug hit against physical hardware: get_waveform() crashed with
    `ValueError: could not convert string to float: '\\n'` when the scope's WAV:DATA? reply had a
    trailing comma right before the terminating newline - splitting on ',' produced a token that
    was just a newline, which float() can't parse. (legacy ASCII/NORM path, binary=False,
    full_memory=False below)
  - A second real bug: get_waveform() always used :WAV:MODE NORM (screen-only) and ASCII
    transfer, so get_all_waveforms() returned only a small fraction of the acquired record and
    did so slowly. Fixed by defaulting to :WAV:MODE RAW (full acquisition memory, chunked across
    multiple :WAV:DATA? queries since the scope caps how much it returns per query) and binary
    (BYTE) transfer, with ASCII kept as an opt-out (binary=False) and screen-only kept as an
    opt-out (full_memory=False) for compatibility/quick-live-look use cases.
"""

import pylogfile.base as plf

from constellation.relay import CommandRelay
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

def make_log():
	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL
	return log

class _FakeRelay(CommandRelay):
	""" Simulates a Rigol DS1000Z's WAV subsystem, including the fact that a real scope caps how
	many points it returns per single :WAV:DATA? query regardless of format (modeled here via
	`chunk_size`), so get_waveform() must loop to read a full record larger than one chunk. """

	def __init__(self, total_points=10, chunk_size=4, trig_status="RUN", mdepth_auto=False, enabled_channels=(1,), stop_settle_polls=0):
		super().__init__()
		self.total_points = total_points
		self.chunk_size = chunk_size
		self.trig_status = trig_status
		self.mdepth_auto = mdepth_auto
		self.enabled_channels = set(enabled_channels)
		self.write_log = []
		# Simulates the real DS1000Z's confirmed behavior: :STOP doesn't take effect
		# instantaneously - :TRIGger:STATus? keeps reporting the old status for a few polls
		# before actually settling to STOP.
		self.stop_settle_polls = stop_settle_polls
		self._stop_polls_remaining = 0

	def connect(self):
		return True

	def close(self):
		pass

	def write(self, cmd):
		self.write_log.append(cmd)
		if cmd == ":STOP":
			self._stop_polls_remaining = self.stop_settle_polls
			if self._stop_polls_remaining == 0:
				self.trig_status = "STOP"
		elif cmd == ":RUN":
			self.trig_status = "RUN"
			self._stop_polls_remaining = 0
		return True

	def read(self):
		return True, ""

	def _current_range(self):
		start = stop = None
		for cmd in reversed(self.write_log):
			if start is None and cmd.startswith(":WAV:STAR "):
				start = int(cmd.split(" ")[1])
			if stop is None and cmd.startswith(":WAV:STOP "):
				stop = int(cmd.split(" ")[1])
			if start is not None and stop is not None:
				break
		return start, stop

	def _next_chunk_range(self):
		start, stop = self._current_range()
		stop = min(stop, self.total_points)
		n = max(0, min(stop - start + 1, self.chunk_size))
		return start, n

	def query(self, cmd):
		c = cmd.strip()
		if c == "*IDN?":
			return True, "RIGOL TECHNOLOGIES,DS1054Z,FAKE,1.0"
		if c.startswith(":CHAN") and c.endswith(":DISP?"):
			return True, "1" if int(c[5]) in self.enabled_channels else "0"
		if c == ":TRIGger:STATus?":
			if self._stop_polls_remaining > 0:
				self._stop_polls_remaining -= 1
				if self._stop_polls_remaining == 0:
					self.trig_status = "STOP"
				return True, "RUN"  # still mid-transition
			return True, self.trig_status
		if c == ":ACQuire:MDEPth?":
			return True, ("AUTO" if self.mdepth_auto else str(self.total_points))
		if c == ":ACQuire:SRATe?":
			return True, "1e6"  # 1 Msample/s
		if c == ":TIM:MAIN:SCAL?":
			# sample_rate * timebase_per_div * 12 must equal total_points for the AUTO-fallback test
			return True, str(self.total_points / 1e6 / 12)
		if c == ":WAV:PRE?":
			# format,type,points,count,xincrement,xorigin,xreference,yincrement,yorigin,yreference
			return True, f"0,2,{self.total_points},1,1e-6,0.0,0,0.04,127,0"
		if c in ("WAV:DATA?", ":WAV:DATA?"):
			start, n = self._next_chunk_range()
			vals = [str(1.0 + i) for i in range(start - 1, start - 1 + n)]
			payload = ",".join(vals)
			header = "#9" + f"{len(payload):09d}"
			return True, header + payload + "\n"
		return True, "0"

	def query_binary(self, cmd, datatype='B'):
		if cmd in ("WAV:DATA?", ":WAV:DATA?"):
			start, n = self._next_chunk_range()
			codes = [(127 + ((start - 1 + i) % 5)) for i in range(n)]
			return True, codes
		return False, []

def make_osc(**relay_kwargs):
	return RigolDS1000Z("fake-addr", log=make_log(), relay=_FakeRelay(**relay_kwargs))

# ---------------------------------------------------------------------------
# Legacy ASCII / screen-only path (binary=False, full_memory=False)
# ---------------------------------------------------------------------------

def _single_shot_data_query(relay, payload):
	""" Overrides relay.query() so a WAV:DATA? request returns `payload` exactly once, then an
	empty reply thereafter - so get_waveform()'s chunking loop (which now walks the full
	documented 1-1200 NORM range, not just however many values a single reply happens to
	contain) terminates naturally after the first (only) real chunk, matching these tests'
	single-small-reply intent. """
	served = {"done": False}
	orig = relay.query

	def _query(cmd):
		if cmd.strip() in ("WAV:DATA?", ":WAV:DATA?"):
			if served["done"]:
				return True, "#900000000" + "\n"
			served["done"] = True
			return True, payload
		return orig(cmd)

	relay.query = _query

def test_legacy_ascii_handles_trailing_comma_before_newline():
	relay = _FakeRelay(total_points=3, chunk_size=10)
	# Force a reply with a trailing comma right before the newline, reproducing the original bug.
	_single_shot_data_query(relay, "#900000010" + "0" + "1.0,2.0,3.0,\n")
	osc = RigolDS1000Z("fake-addr", log=make_log(), relay=relay)

	wf = osc.get_waveform(1, binary=False, full_memory=False)

	assert wf["volt_V"] == [1.0, 2.0, 3.0]

def test_legacy_ascii_handles_clean_reply_without_trailing_comma():
	relay = _FakeRelay(total_points=3, chunk_size=10)
	_single_shot_data_query(relay, "#900000010" + "0" + "1.0,2.0,3.0\n")
	osc = RigolDS1000Z("fake-addr", log=make_log(), relay=relay)

	wf = osc.get_waveform(1, binary=False, full_memory=False)

	assert wf["volt_V"] == [1.0, 2.0, 3.0]

def test_legacy_mode_does_not_touch_run_stop_state():
	osc = make_osc(total_points=3, chunk_size=10, trig_status="RUN")
	osc.get_waveform(1, binary=False, full_memory=False)

	assert ":STOP" not in osc.relay.write_log
	assert ":RUN" not in osc.relay.write_log
	assert any("WAV:MODE NORM" in c for c in osc.relay.write_log)

def test_full_memory_waits_for_stop_to_actually_take_effect(monkeypatch):
	""" Regression test for a real hardware bug: :STOP does not take effect instantaneously -
	:TRIGger:STATus? kept reporting RUN for a bit after :STOP was sent. Proceeding immediately
	into :WAV:MODE RAW etc. while the scope was still mid-transition made the real DS1000Z
	reject the following commands ("Cannot operate now!" on its screen) and hang the next query
	until timeout. get_waveform() must poll until the scope actually confirms STOP. """
	import constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr as rigol_mod
	monkeypatch.setattr(rigol_mod.time, "sleep", lambda s: None)  # don't actually wait in the test

	osc = make_osc(total_points=10, chunk_size=4, trig_status="RUN", stop_settle_polls=3)

	wf = osc.get_waveform(1)

	assert len(wf["volt_V"]) == 10
	assert osc.relay.trig_status == "RUN"  # resumed afterward
	assert osc.relay._stop_polls_remaining == 0  # the settle countdown was allowed to finish

# ---------------------------------------------------------------------------
# New default: full-memory binary transfer, chunked across multiple queries
# ---------------------------------------------------------------------------

def test_full_memory_binary_reads_the_entire_record_via_chunking():
	""" total_points (10) exceeds a single query's chunk_size (4), so get_waveform() must issue
	multiple :WAV:DATA? queries and concatenate them to get the whole record - this is the direct
	fix for 'I get maybe 1/10th the available time points'. """
	osc = make_osc(total_points=10, chunk_size=4)

	wf = osc.get_waveform(1)  # binary=True, full_memory=True by default

	assert len(wf["volt_V"]) == 10
	assert len(wf["time_s"]) == 10
	assert any("WAV:MODE RAW" in c for c in osc.relay.write_log)
	assert any("WAV:FORM BYTE" in c for c in osc.relay.write_log)

def test_full_memory_stops_and_resumes_acquisition_when_running():
	osc = make_osc(total_points=10, chunk_size=4, trig_status="RUN")

	osc.get_waveform(1)

	assert ":STOP" in osc.relay.write_log
	assert ":RUN" in osc.relay.write_log
	assert osc.relay.write_log.index(":STOP") < osc.relay.write_log.index(":RUN")
	assert osc.relay.trig_status == "RUN"  # resumed to its original state

def test_full_memory_does_not_toggle_run_if_already_stopped():
	osc = make_osc(total_points=10, chunk_size=4, trig_status="STOP")

	osc.get_waveform(1)

	assert ":STOP" not in osc.relay.write_log
	assert ":RUN" not in osc.relay.write_log
	assert osc.relay.trig_status == "STOP"

def test_full_memory_resumes_acquisition_even_if_read_fails():
	osc = make_osc(total_points=10, chunk_size=4, trig_status="RUN")
	osc.relay.query_binary = lambda cmd, datatype='B': (_ for _ in ()).throw(RuntimeError("simulated failure"))

	wf = osc.get_waveform(1)

	assert wf["volt_V"] == []
	assert ":RUN" in osc.relay.write_log
	assert osc.relay.trig_status == "RUN"

def test_max_points_caps_the_read():
	osc = make_osc(total_points=10, chunk_size=4)

	wf = osc.get_waveform(1, max_points=5)

	assert len(wf["volt_V"]) == 5

def test_full_memory_ascii_also_chunks_correctly():
	osc = make_osc(total_points=10, chunk_size=4)

	wf = osc.get_waveform(1, binary=False)

	assert len(wf["volt_V"]) == 10
	assert any("WAV:FORM ASCII" in c for c in osc.relay.write_log)

def test_full_memory_resolves_auto_memory_depth_via_sample_rate_and_timebase():
	""" :ACQuire:MDEPth? can return the literal string 'AUTO' instead of a number - get_waveform()
	must fall back to computing the depth from sample rate x timebase x 12 divisions rather than
	crashing or silently reading nothing. """
	osc = make_osc(total_points=10, chunk_size=4, mdepth_auto=True)

	wf = osc.get_waveform(1)

	assert len(wf["volt_V"]) == 10

def test_never_requests_a_stop_point_beyond_total_points():
	""" Regression test for the actual hardware bug this was fixing: the previous implementation
	set :WAV:STOP to an arbitrary huge sentinel value, which exceeds the DS1000Z's documented
	valid range ("1 to the current memory depth") and was rejected by the instrument. Every
	:WAV:STOP this driver sends must stay at or below total_points. """
	osc = make_osc(total_points=10, chunk_size=4)

	osc.get_waveform(1)

	for cmd in osc.relay.write_log:
		if cmd.startswith(":WAV:STOP "):
			assert int(cmd.split(" ")[1]) <= 10

# ---------------------------------------------------------------------------
# get_all_waveforms() batching (stop/resume once, not once per channel)
# ---------------------------------------------------------------------------

def test_get_all_waveforms_stops_and_resumes_only_once():
	""" Regression test: get_all_waveforms() used to call get_waveform() per channel, and each
	call independently stopped + resumed acquisition - for N enabled channels that's N redundant
	stop/resume cycles (extra round trips, and each channel's "full memory" read coming from a
	different acquisition/trigger event instead of one consistent capture). """
	osc = make_osc(total_points=10, chunk_size=4, trig_status="RUN", enabled_channels=(1, 2, 3))

	waveforms = osc.get_all_waveforms()

	assert len(waveforms) == 3
	assert osc.relay.write_log.count(":STOP") == 1
	assert osc.relay.write_log.count(":RUN") == 1
	assert osc.relay.trig_status == "RUN"

def test_get_all_waveforms_does_not_stop_for_full_memory_false():
	osc = make_osc(total_points=10, chunk_size=4, trig_status="RUN", enabled_channels=(1, 2))

	osc.get_all_waveforms(binary=False, full_memory=False)

	assert ":STOP" not in osc.relay.write_log
	assert ":RUN" not in osc.relay.write_log

# ---------------------------------------------------------------------------
# DirectSCPIRelay: explicit timeout (prevents an unresponsive instrument/malformed reply from
# hanging the whole process indefinitely instead of raising a catchable error)
# ---------------------------------------------------------------------------

def test_direct_scpi_relay_sets_an_explicit_timeout(monkeypatch):
	from constellation.relay import DirectSCPIRelay

	class _FakeResource:
		def __init__(self):
			self.timeout = None

	fake_inst = _FakeResource()
	relay = DirectSCPIRelay(timeout_ms=4242)
	monkeypatch.setattr(relay.rm, "open_resource", lambda addr: fake_inst)
	relay.configure("fake-addr", make_log())

	assert relay.connect() is True
	assert fake_inst.timeout == 4242
