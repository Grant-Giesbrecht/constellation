''' Driver for the QuantAsylum QA403 audio analyzer.

The QA40x has no command interface of its own: a PC application drives it over USB and serves a REST
API (JSON over HTTP, port 9402). This driver speaks that API through an HTTPRelay, so `address` is the
server's base URL - "http://localhost:9402" when the application runs on the same machine. Channels
accept 1/2 or "Left"/"Right", and always mean the physical inputs.

Written against QA40x-rs, the community Rust application (the only option on macOS). QuantAsylum's
official application serves the same API and the commands here follow its documented signatures, but
this driver has not been run against it. Every reply carries a SessionId, which is how the driver
tells the two apart ("qa40x-rs" is constant on QA40x-rs).

Triggering (QA403 only):
  The analyzer only measures when told to capture, and every data or measurement endpoint reports on
  the most recent capture, however old. set_trigger_policy() chooses what the data getters do:
    TRIGGER_ON_GET ("on_get", the default)  capture before every data/measurement getter call.
    TRIGGER_MANUAL ("manual")               only send_manual_trigger() captures.
  Each data/measurement getter also takes a keyword-only `acquire` flag: None follows the policy,
  True/False overrides it for that call. To take several measurements from one capture, use the
  manual policy (or acquire=False) after send_manual_trigger(). Every capture plays the enabled
  generators.

Where the API falls short of the AudioAnalyzer category, and how this driver handles it:
  * Not SCPI: no *IDN?/*RST, so query_id() and preset() are overridden (is_scpi=False).
  * No setting can be read back, so every settings getter is @feature_unavailable, and the driver
    runs with blind_state_update: tracked state holds what was sent. A setting that fails validation
    or that the analyzer refuses raises inside the driver method, which stops @superreturn before
    anything is recorded.
  * /Settings/AudioGen sets a generator's state, frequency and amplitude in one command. Each
    category setter sends all three - the new value plus the last values sent, with any field never
    sent this session taken from GENERATOR_DEFAULTS - and records all three. Enabling a generator
    whose frequency or amplitude was never set is refused. set_generator() is a QA403-only shortcut
    that sends all three in one call.

QA40x-rs behaviour worth knowing (read from its src-tauri/src/rest.rs, 2026-09, and checked against
v0.4.0 where noted):
  * Left and Right are swapped in every reply (reported from hardware use; the official application
    is not swapped). The driver reads the opposite key; see _server_key().
  * /Data/Time and /Data/Frequency are in digital full-scale units, not volts - the input range and
    factory calibration are applied only inside the measurement endpoints (checked: 45.53 dB below the
    calibrated level on both channels). The driver converts them; see _volts_per_unit().
  * SessionId is the constant "qa40x-rs" and /AcquisitionBusy always answers "False";
    /Acquisition and /AcquisitionAsync are the same synchronous call. A loop that waits for the
    SessionId to change never finishes, so send_manual_trigger() uses the blocking POST /Acquisition.
  * Only a REST acquisition updates the capture the REST endpoints read (rest.rs is the only writer)
    - acquisitions made in the application's own window do not.
  * /PeakDbv ignores its band and returns the time-domain peak (checked: identical values for
    20-20000, 900-1100, 4900-5100 and 11000-12000 Hz). get_peak_level() warns and takes the band peak
    from the capture's spectrum instead. /ThdnDb and /SnrDb also ignore their band limits.
  * Dx in /Data/Time is printed with six decimals, which truncates the sample period (1/384000 s
    arrives as "0.000003", 15% short). The time step is snapped to the nearest supported rate.
  * /Settings/Default leaves Gen1 playing (the official application's leaves it off), so preset()
    switches both generators off explicitly.

User-facing guide, including multi-analyzer setups: docs/quantasylum_qa403.md
API reference: https://github.com/QuantAsylum/QA40x/wiki/QA40x-API
'''

import base64
import json
import math

import numpy as np

from constellation.base import *
from constellation.relay import HTTPRelay
from constellation.instrument_control.audio_analyzer.audio_analyzer_ctg import *

# SessionId every QA40x-rs reply carries. Used to recognise its swapped channels and uncalibrated /Data.
QA40X_RS_SESSION = "qa40x-rs"

_NO_READBACK = "the QA40x REST API cannot read settings back; the tracked state holds the last value sent"


def _url_number(x) -> str:
	''' The official application parses Hz and dB as integers, so integral values are sent
	without a decimal point. '''
	x = float(x)
	return str(int(x)) if x.is_integer() else repr(x)

def _parse_number(text) -> float:
	''' Numbers arrive as strings - with the host locale's decimal separator from the official
	application, and as "-∞" for a zero level from QA40x-rs. '''
	text = str(text).strip()
	if text in ("-∞", "-Infinity", "-inf"):
		return float("-inf")
	return float(text.replace(",", "."))

def _reports_connected(body:str) -> bool:
	''' Probe check for GET /Status/Connection: the server answering is not enough, the analyzer
	behind it must be attached. '''
	try:
		return str(json.loads(body).get("Value", "")).lower() == "true"
	except (ValueError, AttributeError):
		return False


class QuantAsylumQA403(AudioAnalyzer):

	CHANNEL_NAMES = {1: "Left", 2: "Right"}

	SAMPLE_RATES_Hz = (48000, 96000, 192000, 384000)
	INPUT_RANGES_dBV = (0, 6, 12, 18, 24, 30, 36, 42)
	WINDOWS = ("Rectangle", "Bartlett", "Hamming", "Hann", "FlatTop")
	AMPLITUDE_RANGE_dBV = (-120.0, 18.0)
	MIN_BUFFER_SIZE = 1024

	# Sent for a generator field never sent this session - /Settings/AudioGen needs all three.
	GENERATOR_DEFAULTS = {"enable": False, "freq": 1000.0, "amplitude": -10.0}

	TRIGGER_ON_GET = "on_get"
	TRIGGER_MANUAL = "manual"
	TRIGGER_POLICIES = (TRIGGER_ON_GET, TRIGGER_MANUAL)

	# The getters the trigger policy applies to.
	_TRIGGERED_GETTERS = ("get_waveform", "get_spectrum", "get_rms_level", "get_peak_level", "get_peak_freq", "get_thd", "get_thdn", "get_snr")

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, **kwargs):

		if relay is None:
			relay = HTTPRelay(probe_cmd="GET /Status/Connection", probe_check=_reports_connected, interface="usb")

		# Built before super().__init__(), which connects (and seeds dummy state).
		self._sent_generators = {gen: {"enable": None, "freq": None, "amplitude": None} for gen in (1, 2)}
		self._warned_peak_band = False
		self._trigger_policy = self.TRIGGER_ON_GET

		super().__init__(address, log, relay=relay, is_scpi=False, num_channels=2, num_generators=2, **kwargs)

		# The getters are unavailable, so setters cannot confirm by reading back - see module notes.
		self.blind_state_update = True

	def _query_json(self, cmd:str) -> dict:
		''' query(), with the reply decoded as a JSON object. None if the query failed or the reply
		was not a JSON object. '''

		text = self.query(cmd)
		if not text:
			return None

		try:
			payload = json.loads(text)
		except ValueError as e:
			self.error(f"Reply to >@:LOCK{cmd}@:UNLOCK< was not valid JSON. ({e})", detail=f"Reply: {truncate_str(text, 200)}")
			return None

		if not isinstance(payload, dict):
			self.error(f"Reply to >@:LOCK{cmd}@:UNLOCK< was not a JSON object.", detail=f"Reply: {truncate_str(text, 200)}")
			return None

		return payload

	def _server_key(self, index:int, payload:dict) -> str:
		''' The JSON key holding physical channel `index` in `payload`. QA40x-rs reports Left and
		Right swapped; the official application does not. '''

		if payload.get("SessionId") == QA40X_RS_SESSION:
			return self.CHANNEL_NAMES[3 - index]
		return self.CHANNEL_NAMES[index]

	def query_id(self) -> None:
		''' Replaces the *IDN? check: identifies the analyzer through its control application, and
		counts it offline unless the application reports the analyzer attached. The API reports no
		model, so hardware is never verified. '''

		server = self._query_json("GET /")
		connection = self._query_json("GET /Status/Connection") if server is not None else None

		if server is None or str((connection or {}).get("Value", "")).lower() != "true":
			if server is not None:
				self.error(f"The REST server at >{self.address}< is running, but reports no analyzer attached.")
			self.id.idn_model = ""
			self.online = False
			self.debug(f"Connection state: >OFFLINE<")
			return

		version = self._query_json("GET /Status/Version") or {}
		self.id.idn_model = f"{server.get('Value', 'QA40x REST API')}, firmware {version.get('Value', '?')}"
		self.online = True
		self.debug(f"Connection state: >ONLINE<", detail=f"Identity: {self.id.idn_model}")

	def preset(self) -> None:
		''' Replaces *RST: resets through /Settings/Default, then sends the settings that reset
		explicitly, since control applications disagree on the defaults - the result is the same on
		either, with both generators off. The trigger policy is not changed. '''

		self.debug(f"Preset.", detail=f"{self.id}")

		if not self.write("PUT /Settings/Default"):
			return

		self.set_buffer_size(32768)
		self.set_window("FlatTop")
		for gen in self._sent_generators:
			self.set_generator_enable(gen, False)

	# ==================================================================================
	# Trigger policy (QA403 only)
	# ==================================================================================

	def set_trigger_policy(self, policy:str) -> None:
		''' QA403 only - not part of the AudioAnalyzer API. Chooses whether the data and measurement
		getters capture before every call (TRIGGER_ON_GET, the default) or only when
		send_manual_trigger() is called (TRIGGER_MANUAL). A getter's `acquire` flag overrides this
		for one call. '''

		if policy not in self.TRIGGER_POLICIES:
			self.error(f"Invalid trigger policy >{policy}<. Allowed values: {', '.join(self.TRIGGER_POLICIES)}.")
			return

		self._trigger_policy = policy
		self.debug(f"Trigger policy set to >{policy}<.")

	def get_trigger_policy(self) -> str:
		''' QA403 only. The policy set by set_trigger_policy(). '''
		return self._trigger_policy

	def _capture_for_getter(self, acquire:bool) -> bool:
		''' Captures first if `acquire` says so - or, when it is None, if the trigger policy does.

		Returns:
			bool: False only if a capture was needed and failed.
		'''

		if acquire is None:
			acquire = self._trigger_policy == self.TRIGGER_ON_GET

		if not acquire:
			return True

		if self.send_manual_trigger():
			return True

		self.error("Capture failed, so the measurement was not read.")
		return False

	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Applies the trigger policy in dummy mode, where the driver methods that apply it for
		hardware never run. '''

		acquire = kwargs.pop("acquire", None)

		if func_name in self._TRIGGERED_GETTERS:
			channel = args[0] if args else kwargs.get("channel")
			if self.channel_index(channel) is None or not self._capture_for_getter(acquire):
				return None

		return super().dummy_responder(func_name, *args, **kwargs)

	# ==================================================================================
	# Settings
	# ==================================================================================

	def _send_setting(self, cmd:str) -> None:
		''' Sends a setting, raising if the analyzer refuses it - see module notes. '''
		if not self.write(cmd):
			raise RuntimeError(f"The analyzer did not accept >{cmd}<; the setting was not recorded.")

	@staticmethod
	def _require_choice(label:str, value, choices, unit:str="") -> None:
		if value not in choices:
			raise ValueError(f"Invalid {label} >{value}{unit}<. Allowed values: {', '.join(str(c) for c in choices)}.")

	@superreturn
	def set_sample_rate(self, rate_Hz:int):
		self._require_choice("sample rate", rate_Hz, self.SAMPLE_RATES_Hz, " Hz")
		self._send_setting(f"PUT /Settings/SampleRate/{int(rate_Hz)}")

	@feature_unavailable(_NO_READBACK)
	def get_sample_rate(self):
		pass

	@superreturn
	def set_buffer_size(self, size:int):
		if int(size) != size or size < self.MIN_BUFFER_SIZE or (int(size) & (int(size) - 1)) != 0:
			raise ValueError(f"Invalid buffer size >{size}<. Must be a power of two, at least {self.MIN_BUFFER_SIZE}.")
		self._send_setting(f"PUT /Settings/BufferSize/{int(size)}")

	@feature_unavailable(_NO_READBACK)
	def get_buffer_size(self):
		pass

	@superreturn
	def set_input_range(self, range_dBV:float):
		self._require_choice("input range", range_dBV, self.INPUT_RANGES_dBV, " dBV")
		self._send_setting(f"PUT /Settings/Input/Max/{int(range_dBV)}")

	@feature_unavailable(_NO_READBACK)
	def get_input_range(self):
		pass

	@superreturn
	def set_window(self, window:str):
		self._require_choice("window", window, self.WINDOWS)
		self._send_setting(f"PUT /Settings/Windowing/{window}")

	@feature_unavailable(_NO_READBACK)
	def get_window(self):
		pass

	def _send_generator(self, generator:int, **change) -> None:
		''' Sends one generator's full /Settings/AudioGen command with `change` applied, and records
		all three fields - see module notes. Raises if the command is invalid or refused. '''

		if generator not in self._sent_generators:
			raise ValueError(f"Invalid generator >{generator}<. Valid generators: {list(self._sent_generators)}.")

		fields = {**self._sent_generators[generator], **change}

		if fields["enable"] and (fields["freq"] is None or fields["amplitude"] is None):
			raise ValueError(f"Generator >{generator}< cannot be enabled before its frequency and amplitude are set.")

		fields = {name: (self.GENERATOR_DEFAULTS[name] if value is None else value) for name, value in fields.items()}
		fields = {"enable": bool(fields["enable"]), "freq": float(fields["freq"]), "amplitude": float(fields["amplitude"])}

		if not fields["freq"] > 0:
			raise ValueError(f"Invalid generator frequency >{fields['freq']} Hz<. Must be positive.")

		lo, hi = self.AMPLITUDE_RANGE_dBV
		if not lo <= fields["amplitude"] <= hi:
			raise ValueError(f"Invalid generator amplitude >{fields['amplitude']} dBV<. Must be within [{lo}, {hi}] dBV.")

		on_off = "On" if fields["enable"] else "Off"
		self._send_setting(f"PUT /Settings/AudioGen/Gen{generator}/{on_off}/{_url_number(fields['freq'])}/{_url_number(fields['amplitude'])}")
		self._sent_generators[generator] = fields

		# All three fields were sent, so all three are recorded - through the category's own
		# tracking, not the driver methods, which would send them again.
		AudioAnalyzer.set_generator_freq(self, generator, fields["freq"])
		AudioAnalyzer.set_generator_amplitude(self, generator, fields["amplitude"])
		AudioAnalyzer.set_generator_enable(self, generator, fields["enable"])

	def set_generator(self, generator:int, enable:bool, freq_Hz:float, amplitude_dBV:float) -> None:
		''' QA403 only - not part of the AudioAnalyzer API. Sets a generator's state, frequency and
		amplitude in the single command the instrument takes them in, rather than one command per
		category setter.
		'''

		try:
			self._send_generator(generator, enable=enable, freq=freq_Hz, amplitude=amplitude_dBV)
		except (ValueError, RuntimeError) as e:
			self.error(f"{e}")

	@superreturn
	def set_generator_enable(self, generator:int, enable:bool):
		self._send_generator(generator, enable=enable)

	@feature_unavailable(_NO_READBACK)
	def get_generator_enable(self, generator:int):
		pass

	@superreturn
	def set_generator_freq(self, generator:int, freq_Hz:float):
		self._send_generator(generator, freq=freq_Hz)

	@feature_unavailable(_NO_READBACK)
	def get_generator_freq(self, generator:int):
		pass

	@superreturn
	def set_generator_amplitude(self, generator:int, amplitude_dBV:float):
		self._send_generator(generator, amplitude=amplitude_dBV)

	@feature_unavailable(_NO_READBACK)
	def get_generator_amplitude(self, generator:int):
		pass

	# ==================================================================================
	# Triggering and data
	# ==================================================================================

	@superreturn
	def send_manual_trigger(self):
		with self.long_operation():
			return self.write("POST /Acquisition")

	def _data_block(self, domain:str, channel):
		''' Fetches /Data/<domain>/Input and decodes one channel: base64 of little-endian float64.

		Returns:
			tuple: (numpy array of values, Dx string, SessionId), or (None, None, None) on failure.
		'''

		index = self.channel_index(channel)
		if index is None:
			return None, None, None

		with self.long_operation():
			payload = self._query_json(f"GET /Data/{domain}/Input")
		if payload is None:
			return None, None, None

		key = self._server_key(index, payload)
		try:
			values = np.frombuffer(base64.b64decode(payload[key]), dtype="<f8")
			return values, payload["Dx"], payload.get("SessionId")
		except (KeyError, ValueError, TypeError) as e:
			self.error(f"Could not decode >{key}< from /Data/{domain}/Input. ({e})")
			return None, None, None

	def _volts_per_unit(self, channel, samples=None) -> float:
		''' Factor converting QA40x-rs's /Data values to volts.

		QA40x-rs serves /Data in digital full-scale units (a full-scale sample is 1.0; a full-scale
		sine's spectral line reads -3.01 dBFS) and applies the input range and factory calibration only
		inside its measurement endpoints. Its /PeakDbv is the calibrated time-domain peak of the same
		capture, so comparing it with the raw peak recovers the exact factor - range and trim included -
		without assuming either.

		Args:
			channel: Channel to calibrate.
			samples: That channel's raw /Data/Time samples, if already fetched.

		Returns:
			float: Volts per unit, or None if it cannot be determined.
		'''

		if samples is None:
			samples, _, _ = self._data_block("Time", channel)
			if samples is None:
				return None

		raw_peak = float(np.max(np.abs(samples))) if len(samples) > 0 else 0.0
		peak_dBV = self._channel_reading("GET /PeakDbv/20/20000", channel)

		if peak_dBV is None or not math.isfinite(peak_dBV) or raw_peak <= 0:
			self.error(f"Cannot convert channel >{channel}< data to volts: the capture has no usable peak.")
			return None

		return 10**(peak_dBV / 20) / raw_peak

	def _time_step(self, dx_text) -> float:
		''' Snaps a reported sample period to the nearest supported rate - see the module notes on
		QA40x-rs's six-decimal Dx. '''

		dx = _parse_number(dx_text)
		if not dx > 0:
			return dx
		rate = min(self.SAMPLE_RATES_Hz, key=lambda r: abs(math.log(r * dx)))
		return 1.0 / rate

	def _ready_to_read(self, channel, acquire:bool) -> bool:
		''' Validates the channel before capturing (so a bad channel doesn't cost a capture), then
		applies the trigger policy. '''
		return self.channel_index(channel) is not None and self._capture_for_getter(acquire)

	@superreturn
	def get_waveform(self, channel, *, acquire:bool=None):
		if not self._ready_to_read(channel, acquire):
			return None

		values, dx, session = self._data_block("Time", channel)
		if values is None:
			return None

		if session == QA40X_RS_SESSION:
			scale = self._volts_per_unit(channel, samples=values)
			if scale is None:
				return None
			values = values * scale

		dt = self._time_step(dx)
		return {"x": (np.arange(len(values)) * dt).tolist(), "y": values.tolist(), "x_units": "s", "y_units": "V"}

	def _read_spectrum(self, channel) -> dict:
		''' The most recent capture's spectrum in dBV, without capturing or recording it in state. '''

		values, dx, session = self._data_block("Frequency", channel)
		if values is None:
			return None

		if session == QA40X_RS_SESSION:
			scale = self._volts_per_unit(channel)
			if scale is None:
				return None
			values = values * scale

		df = _parse_number(dx)
		level_dBV = 20 * np.log10(np.maximum(values, 1e-12))
		return {"x": (np.arange(len(values)) * df).tolist(), "y": level_dBV.tolist(), "x_units": "Hz", "y_units": "dBV"}

	@superreturn
	def get_spectrum(self, channel, *, acquire:bool=None):
		if not self._ready_to_read(channel, acquire):
			return None
		return self._read_spectrum(channel)

	def _reading_from(self, payload:dict, index:int, cmd:str) -> float:
		''' Picks physical channel `index` out of a measurement reply. '''

		key = self._server_key(index, payload)
		try:
			return _parse_number(payload[key])
		except (KeyError, ValueError) as e:
			self.error(f"Reply to >@:LOCK{cmd}@:UNLOCK< had no usable >{key}< value. ({e})")
			return None

	def _channel_reading(self, cmd:str, channel) -> float:
		''' Runs a measurement query and returns `channel`'s value from its reply. '''

		index = self.channel_index(channel)
		if index is None:
			return None

		payload = self._query_json(cmd)
		if payload is None:
			return None

		return self._reading_from(payload, index, cmd)

	@superreturn
	def get_rms_level(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, *, acquire:bool=None):
		if not self._ready_to_read(channel, acquire):
			return None
		return self._channel_reading(f"GET /RmsDbv/{_url_number(f_lo_Hz)}/{_url_number(f_hi_Hz)}", channel)

	@superreturn
	def get_peak_level(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, *, acquire:bool=None):
		''' Sends /PeakDbv/{lo}/{hi}. QA40x-rs ignores that band, so against it this warns (once per
		driver) and returns the highest bin of the capture's spectrum within the band instead. '''

		if not self._ready_to_read(channel, acquire):
			return None

		index = self.channel_index(channel)
		cmd = f"GET /PeakDbv/{_url_number(f_lo_Hz)}/{_url_number(f_hi_Hz)}"
		payload = self._query_json(cmd)
		if payload is None:
			return None

		if payload.get("SessionId") != QA40X_RS_SESSION:
			return self._reading_from(payload, index, cmd)

		if not self._warned_peak_band:
			self.warning(f"QA40x-rs ignores the band in /PeakDbv, so band peaks are taken from the capture's spectrum instead.")
			self._warned_peak_band = True

		spectrum = self._read_spectrum(index)
		if spectrum is None:
			return None

		x = np.asarray(spectrum["x"])
		y = np.asarray(spectrum["y"])
		in_band = (x >= f_lo_Hz) & (x <= f_hi_Hz)

		if not in_band.any():
			self.error(f"The spectrum has no bin between >{f_lo_Hz} Hz< and >{f_hi_Hz} Hz<.")
			return None

		return float(y[in_band].max())

	@superreturn
	def get_peak_freq(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, *, acquire:bool=None):
		if not self._ready_to_read(channel, acquire):
			return None
		return self._channel_reading(f"GET /PeakHz/{_url_number(f_lo_Hz)}/{_url_number(f_hi_Hz)}", channel)

	@superreturn
	def get_thd(self, channel, fund_Hz:float=1000.0, max_Hz:float=20000.0, *, acquire:bool=None):
		if not self._ready_to_read(channel, acquire):
			return None
		return self._channel_reading(f"GET /ThdDb/{_url_number(fund_Hz)}/{_url_number(max_Hz)}", channel)

	@superreturn
	def get_thdn(self, channel, fund_Hz:float=1000.0, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, *, acquire:bool=None):
		''' NOTE: QA40x-rs ignores the band limits. '''
		if not self._ready_to_read(channel, acquire):
			return None
		return self._channel_reading(f"GET /ThdnDb/{_url_number(fund_Hz)}/{_url_number(f_lo_Hz)}/{_url_number(f_hi_Hz)}", channel)

	@superreturn
	def get_snr(self, channel, fund_Hz:float=1000.0, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, *, acquire:bool=None):
		''' NOTE: QA40x-rs ignores the band limits. '''
		if not self._ready_to_read(channel, acquire):
			return None
		return self._channel_reading(f"GET /SnrDb/{_url_number(fund_Hz)}/{_url_number(f_lo_Hz)}/{_url_number(f_hi_Hz)}", channel)
