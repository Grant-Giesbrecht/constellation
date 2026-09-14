""" Tests for the audio analyzer category, the QuantAsylum QA403 driver, and HTTPRelay.

Nothing here needs hardware or a QA40x application: the driver runs in dummy mode, against a stub
relay that answers the way QA40x-rs does, and HTTPRelay runs against a throwaway local HTTP server.
"""

import base64
import inspect
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest
import pylogfile.base as plf

from constellation.base import FeatureUnavailable
from constellation.relay import CommandRelay, HTTPRelay, RelayErrorKind
from constellation.instrument_control.audio_analyzer.audio_analyzer_ctg import AudioAnalyzer
from constellation.instrument_control.audio_analyzer.drivers.QuantAsylum_QA403_dvr import QuantAsylumQA403, _parse_number, _reports_connected

def make_log():
	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL  # keep test output quiet
	return log

def make_dummy():
	return QuantAsylumQA403("http://localhost:9402", make_log(), dummy=True)

SETTINGS_GETTERS = {"get_sample_rate", "get_buffer_size", "get_input_range", "get_window",
	"get_generator_enable", "get_generator_freq", "get_generator_amplitude"}

# ---------------------------------------------------------------------------
# Category stays instrument-agnostic
# ---------------------------------------------------------------------------

def test_category_has_one_parameter_per_setter():
	""" A setter that takes several parameters at once is an instrument's command format leaking
	into the category - the driver composes combined commands instead. """
	for name, fn in inspect.getmembers(AudioAnalyzer, inspect.isfunction):
		if name.startswith("set_"):
			params = [p for p in inspect.signature(fn).parameters if p not in ("self", "generator", "channel")]
			assert len(params) == 1, f"AudioAnalyzer.{name} sets {params}"

def test_category_does_not_override_transport_or_identity():
	for name in ("write", "query", "read", "query_id", "preset"):
		assert name not in vars(AudioAnalyzer), f"AudioAnalyzer overrides Driver.{name}"

def test_trigger_policy_is_qa403_only():
	for name in ("set_trigger_policy", "get_trigger_policy", "set_generator"):
		assert not hasattr(AudioAnalyzer, name)
		assert hasattr(QuantAsylumQA403, name)

def test_qa403_discloses_that_settings_cannot_be_read_back():
	qa = make_dummy()
	assert SETTINGS_GETTERS <= set(qa.unavailable_features())
	with pytest.raises(FeatureUnavailable):
		qa.get_sample_rate()
	qa.refresh_state()    # a sweep skips them rather than aborting

# ---------------------------------------------------------------------------
# Dummy mode
# ---------------------------------------------------------------------------

def test_dummy_starts_with_a_known_configuration():
	qa = make_dummy()
	assert qa.state.sample_rate == 48000
	assert qa.state.buffer_size == 32768
	assert qa.state.input_range == 18
	assert qa.state.window == "Hann"
	assert qa.state.get(["generators", "enable"], indices=[1]) is True
	assert qa.state.get(["generators", "enable"], indices=[2]) is False
	assert qa.get_trigger_policy() == QuantAsylumQA403.TRIGGER_ON_GET

def test_dummy_settings_are_tracked():
	qa = make_dummy()
	qa.set_sample_rate(96000)
	qa.set_buffer_size(4096)
	qa.set_input_range(6)
	qa.set_window("FlatTop")
	qa.set_generator_freq(2, 2000)

	assert (qa.state.sample_rate, qa.state.buffer_size, qa.state.input_range, qa.state.window) == (96000, 4096, 6, "FlatTop")
	assert qa.state.get(["generators", "freq"], indices=[2]) == 2000

@pytest.mark.parametrize("given,expected", [
	(1, 1), (2, 2), ("1", 1), ("left", 1), ("Left", 1), ("RIGHT", 2),
	("center", None), (3, None), (0, None), (True, None),
])
def test_channels_by_index_or_name(given, expected):
	assert make_dummy().channel_index(given) == expected

def test_dummy_default_policy_captures_on_every_getter_call():
	qa = make_dummy()
	first = qa.get_waveform(1)
	second = qa.get_waveform(1)
	same_capture = qa.get_waveform(1, acquire=False)

	assert first["y"] != second["y"]        # independent noise: two captures
	assert same_capture["y"] == second["y"]

def test_dummy_manual_policy_needs_a_trigger():
	qa = make_dummy()
	qa.set_trigger_policy(QuantAsylumQA403.TRIGGER_MANUAL)

	assert qa.get_spectrum(1) is None                # nothing captured yet
	assert qa.get_thd(1, acquire=True) is not None   # per-call override
	assert qa.send_manual_trigger() is True
	assert qa.get_spectrum(1) is not None

def test_invalid_trigger_policy_is_refused():
	qa = make_dummy()
	qa.set_trigger_policy("sometimes")
	assert qa.get_trigger_policy() == QuantAsylumQA403.TRIGGER_ON_GET

def test_data_getters_accept_channel_names():
	qa = make_dummy()
	spectrum = qa.get_spectrum("left")
	assert qa.state.get(["channels", "spectrum"], indices=[1]) is spectrum
	assert qa.get_peak_level("Right", 900, 1100) == pytest.approx(-16.02, abs=0.05)

def test_preset_switches_generators_off_and_sends_defaults():
	qa = make_dummy()
	qa.preset()
	assert qa.state.get(["generators", "enable"], indices=[1]) is False
	assert qa.state.get(["generators", "enable"], indices=[2]) is False
	assert qa.state.buffer_size == 32768
	assert qa.state.window == "FlatTop"
	assert qa.get_rms_level(1) < -100   # noise floor only

def test_dummy_spectrum_has_the_shape_and_tone_expected():
	qa = make_dummy()
	spectrum = qa.get_spectrum(1)

	assert set(spectrum) == {"x", "y", "x_units", "y_units"}
	assert (spectrum["x_units"], spectrum["y_units"]) == ("Hz", "dBV")
	assert len(spectrum["x"]) == len(spectrum["y"]) == 32768 // 2
	assert qa.get_peak_freq(1, acquire=False) == pytest.approx(1000, abs=1.5)
	assert qa.get_peak_level(1, 900, 1100, acquire=False) == pytest.approx(-10.0, abs=0.05)

def test_dummy_measurements_are_plausible():
	qa = make_dummy()
	qa.set_trigger_policy(QuantAsylumQA403.TRIGGER_MANUAL)
	qa.send_manual_trigger()

	thd = qa.get_thd(1)
	thdn = qa.get_thdn(1)

	assert qa.get_rms_level(1) == pytest.approx(-10.0, abs=0.05)
	assert thd == pytest.approx(-88.8, abs=0.5)       # 2nd at -90 dBc, 3rd at -95 dBc
	assert thd <= thdn < -80
	assert 95 < qa.get_snr(1) < 110

def test_dummy_waveform_is_in_volts_and_seconds():
	qa = make_dummy()
	wav = qa.get_waveform(1)

	assert (wav["x_units"], wav["y_units"]) == ("s", "V")
	assert wav["x"][1] - wav["x"][0] == pytest.approx(1 / 48000)
	assert np.std(wav["y"]) == pytest.approx(10**(-10 / 20), rel=1e-2)

def test_invalid_channel_is_refused():
	qa = make_dummy()
	assert qa.get_spectrum(3) is None

# ---------------------------------------------------------------------------
# Real (non-dummy) path, against a relay that answers like QA40x-rs
# ---------------------------------------------------------------------------

OFFSET_dB = 45.53   # measured on a live QA403 under QA40x-rs; the conversion must not depend on it

def _b64(values):
	return base64.b64encode(np.asarray(values, dtype="<f8").tobytes()).decode()

class StubQA40xRs(CommandRelay):
	""" Answers like QA40x-rs by default: /Data in digital full-scale units, measurements calibrated,
	settings accepted or refused. Records every command. `volts` is keyed by the server's JSON keys -
	on QA40x-rs, "Left" holds the physical right input. Any other `session` answers like the official
	application: /Data already in volts. """

	def __init__(self, analyzer_attached=True, fs=48000, n=4096, session="qa40x-rs"):
		super().__init__()
		self.sent = []
		self.analyzer_attached = analyzer_attached
		self.refuse_settings = False
		self.refuse_acquisition = False
		self.probe_result = True
		self.fs = fs
		self.n = n
		self.tone_Hz = 85 * fs / n     # bin-centred
		t = np.arange(n) / fs
		self.volts = {"Left": 0.01 * np.sin(2 * np.pi * self.tone_Hz * t), "Right": 0.005 * np.sin(2 * np.pi * self.tone_Hz * t)}
		self.session = session
		self.scale = 10**(OFFSET_dB / 20) if session == "qa40x-rs" else 1.0

	def connect(self):
		return True

	def close(self):
		pass

	def read(self):
		return False, ""

	def probe(self, timeout_s=1.5):
		return self.probe_result

	def write(self, cmd):
		return self.query(cmd)[0]

	def query(self, cmd):
		self.sent.append(cmd)
		_, path = HTTPRelay.split_command(cmd)
		parts = [p for p in path.split("/") if p]
		sid = {"SessionId": self.session}

		if not parts:
			body = {**sid, "Value": "qa40x-rs REST API"}
		elif parts == ["Status", "Connection"]:
			body = {**sid, "Value": "True" if self.analyzer_attached else "False"}
		elif parts == ["Status", "Version"]:
			body = {**sid, "Value": "60"}
		elif parts[0] == "Settings":
			if self.refuse_settings:
				self.last_error_kind = RelayErrorKind.USAGE
				return False, ""
			body = {**sid, "Value": "True"}
		elif parts == ["Acquisition"]:
			if self.refuse_acquisition:
				self.last_error_kind = RelayErrorKind.USAGE
				return False, ""
			body = {**sid, "Value": "True"}
		elif parts[:2] == ["Data", "Time"]:
			body = {**sid, "Length": str(self.n), "Dx": f"{1 / self.fs:.6f}",
				**{k: _b64(v / self.scale) for k, v in self.volts.items()}}
		elif parts[:2] == ["Data", "Frequency"]:
			body = {**sid, "Length": str(self.n // 2 + 1), "Dx": f"{self.fs / self.n:.6f}",
				**{k: _b64(np.abs(np.fft.rfft(v / self.scale)) * np.sqrt(2) / self.n) for k, v in self.volts.items()}}
		elif parts[0] == "PeakDbv":
			body = {**sid, **{k: f"{20 * np.log10(np.max(np.abs(v))):.6f}" for k, v in self.volts.items()}}
		else:
			body = {**sid, "Left": "-40.000000", "Right": "-46.000000"}

		self.last_error_kind = RelayErrorKind.NONE
		return True, json.dumps(body)

	def generator_commands(self):
		return [c for c in self.sent if "/AudioGen/" in c]

def make_stubbed(**kwargs):
	relay = StubQA40xRs(**kwargs)
	return QuantAsylumQA403("http://localhost:9402", make_log(), relay=relay), relay

def gen_state(qa, gen):
	return tuple(qa.state.get(["generators", f], indices=[gen]) for f in ("enable", "freq", "amplitude"))

def test_connects_and_identifies_through_the_rest_api():
	qa, _ = make_stubbed()
	assert qa.online
	assert "qa40x-rs" in qa.id.idn_model
	assert "firmware 60" in qa.id.idn_model

def test_offline_when_the_server_has_no_analyzer_attached():
	qa, _ = make_stubbed(analyzer_attached=False)
	assert not qa.online

def test_check_online_uses_the_relay_probe_for_a_non_scpi_instrument():
	qa, relay = make_stubbed()
	qa.check_online()
	assert qa.online
	relay.probe_result = False
	qa.check_online()
	assert not qa.online

@pytest.mark.parametrize("call,expected", [
	(lambda qa: qa.set_sample_rate(96000), "PUT /Settings/SampleRate/96000"),
	(lambda qa: qa.set_buffer_size(8192), "PUT /Settings/BufferSize/8192"),
	(lambda qa: qa.set_input_range(6), "PUT /Settings/Input/Max/6"),
	(lambda qa: qa.set_window("Hann"), "PUT /Settings/Windowing/Hann"),
	(lambda qa: qa.send_manual_trigger(), "POST /Acquisition"),
	(lambda qa: qa.get_rms_level(1, 20, 20000), "GET /RmsDbv/20/20000"),
	(lambda qa: qa.get_thd("left", 1000, 20000), "GET /ThdDb/1000/20000"),
	(lambda qa: qa.get_thdn(1, 1000, 20, 20000), "GET /ThdnDb/1000/20/20000"),
	(lambda qa: qa.get_snr(1, 1000, 20, 20000), "GET /SnrDb/1000/20/20000"),
	(lambda qa: qa.get_peak_freq(1, 20, 20000), "GET /PeakHz/20/20000"),
])
def test_sends_the_documented_commands(call, expected):
	qa, relay = make_stubbed()
	relay.sent.clear()
	call(qa)
	assert expected in relay.sent

def test_getters_capture_first_by_default():
	qa, relay = make_stubbed()
	relay.sent.clear()
	qa.get_thd(1)
	assert relay.sent == ["POST /Acquisition", "GET /ThdDb/1000/20000"]

@pytest.mark.parametrize("policy,acquire,captures", [
	(QuantAsylumQA403.TRIGGER_ON_GET, None, True),
	(QuantAsylumQA403.TRIGGER_ON_GET, False, False),
	(QuantAsylumQA403.TRIGGER_ON_GET, True, True),
	(QuantAsylumQA403.TRIGGER_MANUAL, None, False),
	(QuantAsylumQA403.TRIGGER_MANUAL, True, True),
	(QuantAsylumQA403.TRIGGER_MANUAL, False, False),
])
def test_acquire_flag_overrides_the_policy_for_one_call(policy, acquire, captures):
	qa, relay = make_stubbed()
	qa.set_trigger_policy(policy)
	relay.sent.clear()

	qa.get_rms_level(1, acquire=acquire)
	assert ("POST /Acquisition" in relay.sent) == captures

	relay.sent.clear()
	qa.get_rms_level(1)                          # the override does not stick
	assert ("POST /Acquisition" in relay.sent) == (policy == QuantAsylumQA403.TRIGGER_ON_GET)

@pytest.mark.parametrize("getter", ["get_waveform", "get_spectrum", "get_peak_level"])
def test_one_capture_per_getter_call(getter):
	""" On QA40x-rs these getters make several requests (the volts conversion, the band-peak
	fallback) - still only one capture. """
	qa, relay = make_stubbed()
	relay.sent.clear()
	getattr(qa, getter)("left")
	assert relay.sent.count("POST /Acquisition") == 1

def test_failed_capture_means_no_reading():
	qa, relay = make_stubbed()
	relay.refuse_acquisition = True
	relay.sent.clear()

	assert qa.get_thd(1) is None
	assert not any(c.startswith("GET /ThdDb") for c in relay.sent)

def test_invalid_channel_does_not_cost_a_capture():
	qa, relay = make_stubbed()
	relay.sent.clear()
	assert qa.get_thd(3) is None
	assert relay.sent == []

def test_qa40x_rs_channels_are_unswapped():
	""" QA40x-rs reports Left and Right swapped; channel 1 / "left" must still mean the physical
	left input. """
	qa, _ = make_stubbed()
	assert qa.get_thd(1) == -46.0          # server's "Right" key
	assert qa.get_thd("left") == -46.0
	assert qa.get_thd("Right") == -40.0

def test_official_application_channels_are_read_as_reported():
	qa, _ = make_stubbed(session="official-app-session")
	assert qa.get_thd(1) == -40.0
	assert qa.get_thd("Right") == -46.0

def test_settings_are_recorded_once_sent():
	qa, _ = make_stubbed()
	assert qa.state.sample_rate is None      # unknown until sent: nothing can be read back
	qa.set_sample_rate(96000)
	assert qa.state.sample_rate == 96000

@pytest.mark.parametrize("call,field", [
	(lambda qa: qa.set_sample_rate(44100), "sample_rate"),
	(lambda qa: qa.set_buffer_size(3000), "buffer_size"),
	(lambda qa: qa.set_buffer_size(512), "buffer_size"),
	(lambda qa: qa.set_input_range(5), "input_range"),
	(lambda qa: qa.set_window("Kaiser"), "window"),
])
def test_invalid_setting_is_neither_sent_nor_recorded(call, field):
	qa, relay = make_stubbed()
	relay.sent.clear()
	call(qa)
	assert relay.sent == []
	assert qa.state.get([field]) is None

def test_refused_setting_is_not_recorded_and_does_not_take_the_driver_offline():
	""" With no read-back, a refused setting recorded anyway would be wrong for the rest of the
	session. A 4xx is a usage error, not a lost connection. """
	qa, relay = make_stubbed()
	qa.set_buffer_size(4096)
	relay.refuse_settings = True

	qa.set_buffer_size(8192)
	assert qa.state.buffer_size == 4096
	assert qa.online

def test_generator_setters_each_send_the_full_command_and_record_what_was_sent():
	qa, relay = make_stubbed()

	qa.set_generator_freq(2, 2000)
	assert relay.generator_commands()[-1] == "PUT /Settings/AudioGen/Gen2/Off/2000/-10"
	assert gen_state(qa, 2) == (False, 2000.0, -10.0)     # defaults sent are recorded too

	qa.set_generator_amplitude(2, -3.5)
	assert relay.generator_commands()[-1] == "PUT /Settings/AudioGen/Gen2/Off/2000/-3.5"

	qa.set_generator_enable(2, True)
	assert relay.generator_commands()[-1] == "PUT /Settings/AudioGen/Gen2/On/2000/-3.5"
	assert gen_state(qa, 2) == (True, 2000.0, -3.5)

def test_enabling_a_generator_with_no_frequency_or_amplitude_is_refused():
	""" Enabling it would mean guessing at a tone to put into a DUT. """
	qa, relay = make_stubbed()
	qa.set_generator_enable(1, True)
	assert relay.generator_commands() == []
	assert gen_state(qa, 1) == (None, None, None)

def test_out_of_range_generator_amplitude_is_neither_sent_nor_recorded():
	qa, relay = make_stubbed()
	qa.set_generator_amplitude(1, 30)
	assert relay.generator_commands() == []
	assert gen_state(qa, 1) == (None, None, None)

def test_driver_only_set_generator_sends_one_command():
	qa, relay = make_stubbed()
	qa.set_generator(1, True, 500, -20)
	assert relay.generator_commands() == ["PUT /Settings/AudioGen/Gen1/On/500/-20"]
	assert gen_state(qa, 1) == (True, 500.0, -20.0)

def test_preset_resets_then_sends_defaults_with_generators_off():
	qa, relay = make_stubbed()
	relay.sent.clear()
	qa.preset()

	assert relay.sent == [
		"PUT /Settings/Default",
		"PUT /Settings/BufferSize/32768",
		"PUT /Settings/Windowing/FlatTop",
		"PUT /Settings/AudioGen/Gen1/Off/1000/-10",
		"PUT /Settings/AudioGen/Gen2/Off/1000/-10",
	]
	assert gen_state(qa, 1)[0] is False and gen_state(qa, 2)[0] is False

def test_waveform_is_converted_to_volts_with_an_exact_time_step():
	""" QA40x-rs serves raw full-scale samples, and prints Dx with six decimals ("0.000021" for
	1/48000 s, 0.8% off). """
	qa, relay = make_stubbed()
	wav = qa.get_waveform("left")

	assert max(wav["y"]) == pytest.approx(0.005, rel=1e-5)     # physical left = server's "Right"
	assert wav["x"][1] - wav["x"][0] == pytest.approx(1 / 48000, rel=1e-12)
	assert qa.state.get(["channels", "waveform"], indices=[1]) is wav

def test_spectrum_is_converted_to_dBV():
	qa, relay = make_stubbed()
	spectrum = qa.get_spectrum(2)
	peak = int(np.argmax(spectrum["y"]))

	assert spectrum["x"][peak] == pytest.approx(relay.tone_Hz)
	assert spectrum["y"][peak] == pytest.approx(20 * np.log10(0.01 / np.sqrt(2)), abs=1e-3)   # physical right = server's "Left"

def test_official_application_data_is_used_as_volts():
	qa, relay = make_stubbed(session="official-app-session")
	wav = qa.get_waveform("left")
	assert max(wav["y"]) == pytest.approx(0.01, rel=1e-9)
	assert not any("/PeakDbv/" in c for c in relay.sent)      # no conversion needed

def test_peak_level_on_qa40x_rs_uses_the_band():
	""" QA40x-rs ignores /PeakDbv's band, so the driver takes the band peak from the spectrum. """
	qa, relay = make_stubbed()
	tone_level = 20 * np.log10(0.005 / np.sqrt(2))      # physical left

	assert qa.get_peak_level("left", 900, 1100) == pytest.approx(tone_level, abs=1e-3)
	assert qa.get_peak_level("left", 2000, 3000) < tone_level - 60

def test_peak_level_on_the_official_application_is_the_servers_answer():
	qa, relay = make_stubbed(session="official-app-session")
	relay.sent.clear()
	value = qa.get_peak_level("left", 900, 1100)

	assert value == pytest.approx(20 * np.log10(0.01))
	assert relay.sent == ["POST /Acquisition", "GET /PeakDbv/900/1100"]

def test_number_parsing_handles_locale_and_infinity():
	assert _parse_number("-12,5") == -12.5
	assert _parse_number("-∞") == float("-inf")
	assert _parse_number(" 3.25 ") == 3.25

# ---------------------------------------------------------------------------
# HTTPRelay against a real local server
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):

	def _reply(self):
		self.server.seen.append((self.command, self.path, self.headers.get("Authorization")))

		if self.path == "/missing":
			code, body = 404, {"Error": "unknown endpoint"}
		elif self.path == "/boom":
			code, body = 500, {"Error": "acquisition failed"}
		elif self.path == "/slow":
			time.sleep(0.5)
			code, body = 200, {"Value": "slow"}
		elif self.path == "/Status/Connection":
			code, body = 200, {"Value": self.server.attached}
		else:
			code, body = 200, {"Value": "ok", "path": self.path}

		data = json.dumps(body).encode()
		self.send_response(code)
		self.send_header("Content-Type", "application/json")
		self.send_header("Content-Length", str(len(data)))
		self.end_headers()
		self.wfile.write(data)

	do_GET = _reply
	do_PUT = _reply
	do_POST = _reply

	def log_message(self, *args):
		pass

@pytest.fixture
def http_server():
	server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
	server.daemon_threads = True
	server.seen = []
	server.attached = "True"
	thread = threading.Thread(target=server.serve_forever, daemon=True)
	thread.start()
	yield server
	server.shutdown()
	server.server_close()

def make_relay(address, **kwargs):
	relay = HTTPRelay(**kwargs)
	relay.configure(address, make_log())
	return relay

def served_relay(server, **kwargs):
	return make_relay(f"127.0.0.1:{server.server_address[1]}", **kwargs)

@pytest.mark.parametrize("cmd,expected", [
	("GET /Status/Version", ("GET", "/Status/Version")),
	("put /Settings/BufferSize/32768", ("PUT", "/Settings/BufferSize/32768")),
	("POST /Acquisition", ("POST", "/Acquisition")),
	("/Status/Version", ("GET", "/Status/Version")),
	("Status/Version", ("GET", "/Status/Version")),
	("GET", ("GET", "/")),
])
def test_split_command(cmd, expected):
	assert HTTPRelay.split_command(cmd) == expected

def test_address_without_scheme_is_taken_as_http():
	assert make_relay("localhost:9402/").base_url == "http://localhost:9402"
	assert make_relay("https://bench:9402").base_url == "https://bench:9402"

def test_relay_uses_the_verb_and_returns_the_body(http_server):
	relay = served_relay(http_server)

	assert relay.connect()
	assert relay.write("PUT /Settings/BufferSize/1024")
	ok, body = relay.query("GET /Status/Version")

	assert ok and json.loads(body)["path"] == "/Status/Version"
	assert ("PUT", "/Settings/BufferSize/1024", None) in http_server.seen

@pytest.mark.parametrize("path,kind", [("/missing", RelayErrorKind.USAGE), ("/boom", RelayErrorKind.INSTRUMENT)])
def test_http_errors_are_classified(http_server, path, kind):
	relay = served_relay(http_server)
	assert relay.query(f"GET {path}") == (False, "")
	assert relay.last_error_kind == kind

def _closed_port():
	s = socket.socket()
	s.bind(("127.0.0.1", 0))
	port = s.getsockname()[1]
	s.close()
	return port

def test_unreachable_server_is_a_transport_failure():
	relay = make_relay(f"127.0.0.1:{_closed_port()}")
	assert relay.connect() is False
	assert relay.last_error_kind == RelayErrorKind.TRANSPORT
	assert relay.query("GET /") == (False, "")
	assert relay.last_error_kind == RelayErrorKind.TRANSPORT

def test_long_operation_raises_the_timeout(http_server):
	relay = served_relay(http_server, timeout_s=0.1, long_timeout_s=5.0)

	assert relay.query("GET /slow")[0] is False
	assert relay.last_error_kind == RelayErrorKind.TRANSPORT

	with relay.long_operation():
		assert relay.query("GET /slow")[0] is True
	assert relay._active_timeout_s == 0.1

def test_probe_honours_probe_check(http_server):
	relay = served_relay(http_server, probe_cmd="GET /Status/Connection", probe_check=_reports_connected)

	assert relay.probe() is True
	http_server.attached = "False"
	assert relay.probe() is False
	assert relay.last_error_kind == RelayErrorKind.TRANSPORT

def test_bearer_token_is_sent(http_server):
	relay = served_relay(http_server, token="abc123")
	relay.query("GET /anything")
	assert http_server.seen[-1][2] == "Bearer abc123"

def test_read_is_a_usage_error():
	relay = make_relay("localhost:9402")
	assert relay.read() == (False, "")
	assert relay.last_error_kind == RelayErrorKind.USAGE
