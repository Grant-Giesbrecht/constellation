''' Category class for multifunction data-acquisition (DAQ) devices.

Unlike the SCPI instrument categories, a DAQ (e.g. an NI M-series multifunction card) is NOT a
text/request-response instrument - it has no persistent, queryable configuration registers. A
measurement is defined by building a *task* (a set of channels + timing), running it, and reading
back a block of samples; between tasks the hardware holds essentially no state. Two consequences
shape this category:

  1. Configuration (sample rate, per-channel range/terminal-config/enable, ...) is authoritative in
     the driver's InstrumentState, not on the hardware. The category therefore runs with
     `blind_state_update` set, and every set_* stores its value through `_record()` - there is
     nothing to read back to verify, and refresh_state() has nothing to re-read.
  2. Everything that actually touches hardware is funnelled through a small set of abstract
     `_hw_*` primitives. A concrete driver only implements those (translating them to nidaqmx, or
     comedi, or a reverse-engineered USB protocol); all orchestration, state tracking and dummy
     simulation live here, mirroring how the SCPI categories keep everything but the SCPI strings
     out of the driver file.

Because the `_hw_*` primitives are this category's abstract methods, they are also what
`verification.yaml` records for a DAQ driver (see docs/hardware_verification.md): they are the only
code a DAQ driver author writes, so they are what hardware evidence describes, and their code hash
is what expires it. The public methods above them are shared category code.

Every primitive call goes through `_run_hw()`, which does for a DAQ what Driver.write/query do for a
SCPI driver: refuses to run while offline (or reconnects, per the ReconnectPolicy), lets the relay
classify a failure (a bad channel name is USAGE, an unplugged device is TRANSPORT), and marks the
driver offline only when the device is really gone.

Two ways to take samples, because a DAQ is used two ways:

  - `acquire()` is a SNAPSHOT: build a task, capture `samples_per_channel` samples, close the task.
    Like pressing Single on a scope. Calling it in a loop leaves a gap between captures, during
    which the board is not sampling, and each capture's time base restarts at zero.
  - `start_stream()` / `read_stream()` / `stop_stream()` is a RECORDING: one task runs
    continuously into a buffer on the host, and each read_stream() drains the next contiguous
    block. Chunk k+1 begins exactly one sample period after chunk k ends, and `first_sample` / `t_s`
    keep counting across chunks. `stream(callback)` wraps the three in a loop for "record until I
    say stop". Stream data is returned, never stored in state: an unbounded recording does not
    belong in a serialisable state object (state only tracks `streaming` and each channel's latest
    value). Chunks hold numpy arrays - a long recording in Python lists costs ~4x the memory - and
    `recording.HDF5StreamRecorder` is a ready-made `stream()` callback that writes them to disk.

acquire() results stay plain lists, because they are stored in state and cross labmesh as JSON.

Because a DAQ is not SCPI, drivers in this category are constructed with is_scpi=False, and this
category overrides query_id() to identify the device via `_hw_device_identity()` rather than
"*IDN?".
'''

import datetime
import math
import time

import numpy as np

from constellation.base import *


class AnalogInputChannelState(InstrumentState):
	''' Per-analog-input-channel configuration and last reading. `last_value_V` is flagged as data
	(a measured result), everything else is configuration the user sets. '''

	__state_fields__ = ("enabled", "terminal_config", "range_min_V", "range_max_V", "last_value_V")

	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)

		self.add_param("enabled", unit="bool", value=False)
		self.add_param("terminal_config", unit="CONST", value="differential")
		self.add_param("range_min_V", unit="V", value=-10.0)
		self.add_param("range_max_V", unit="V", value=10.0)

		self.add_param("last_value_V", unit="V", is_data=True, value=None)



class DataAcquisitionState(InstrumentState):
	''' Whole-device DAQ state: global acquisition timing, the per-channel analog-input list, and
	digital-line state. `last_acquisition` holds the most recent buffered capture as a
	JSON-serialisable dict ({"dt_s":..., "sample_rate_Hz":..., "channels":{"0":[...], ...}}) so it
	survives state serialisation and crossing labmesh (which is JSON) unchanged. '''

	__state_fields__ = ("first_channel", "num_ai_channels", "sample_rate_Hz", "samples_per_channel",
		"acquisition_mode", "clock_source", "num_digital_lines", "digital_out", "digital_in",
		"ai_channels", "last_acquisition", "streaming")

	def __init__(self, first_channel:int, num_ai_channels:int, num_digital_lines:int=0, log:plf.LogPile=None):
		super().__init__(log=log)

		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_ai_channels", unit="1", value=num_ai_channels)

		self.add_param("sample_rate_Hz", unit="Hz", value=1000.0)
		self.add_param("samples_per_channel", unit="1", value=1000)
		self.add_param("acquisition_mode", unit="CONST", value="finite")
		self.add_param("clock_source", unit="", value="")

		self.add_param("num_digital_lines", unit="1", value=num_digital_lines)
		# Digital line state kept as {str(line): bool} dicts so they serialise cleanly.
		self.add_param("digital_out", unit="bool", value={})
		self.add_param("digital_in", unit="bool", value={})

		self.add_param("ai_channels", unit="", value=IndexedList(first_channel, num_ai_channels, validate_type=AnalogInputChannelState, log=log))
		for ch_no in self.ai_channels.get_range():
			self.ai_channels[ch_no] = AnalogInputChannelState(log=log)

		self.add_param("last_acquisition", unit="", is_data=True, value=None)

		# Whether a stream is running. A status, not a setting - restoring a saved state must not
		# be read as a request to start one - so it is flagged as data.
		self.add_param("streaming", unit="bool", is_data=True, value=False)



class DataAcquisition(Driver):
	''' Abstract DAQ driver. Concrete drivers implement only the `_hw_*` primitives (see bottom of
	class). Everything user-facing - configuration, single-point reads, buffered acquisition,
	digital I/O, dummy simulation and state tracking - is implemented here. '''

	# --- Terminal (input) configurations ---
	TERM_DIFF = "differential"
	TERM_RSE = "rse"
	TERM_NRSE = "nrse"
	TERM_PSEUDODIFF = "pseudodifferential"
	TERMINAL_CONFIGS = (TERM_DIFF, TERM_RSE, TERM_NRSE, TERM_PSEUDODIFF)

	# --- Acquisition modes ---
	# Timing of acquire()'s snapshot. Continuous recording is start_stream()/read_stream().
	MODE_FINITE = "finite"          # Fixed number of hardware-timed samples, then stop.
	MODE_ON_DEMAND = "on_demand"    # Software-timed, one sample per read (no sample clock).
	ACQUISITION_MODES = (MODE_FINITE, MODE_ON_DEMAND)

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="",
			max_ai_channels:int=16, num_digital_lines:int=0, dummy:bool=False, first_channel:int=0, **kwargs):

		_state = DataAcquisitionState(first_channel, max_ai_channels, num_digital_lines=num_digital_lines, log=log)

		# The running stream, if any: {"specs", "dt", "chunk", "next_sample", "t_start",
		# "started_at"}. Runtime bookkeeping, not state - see the module docstring.
		self._stream = None

		# How the most recent stream ended: {"reason", "samples"}, or None if none has. Lets a
		# consumer such as HDF5StreamRecorder say in its file why a recording stopped.
		self.last_stream_end = None

		# A DAQ is not a SCPI instrument - is_scpi=False disables the base class's *IDN?/*RST/*OPC
		# machinery, which would be meaningless here.
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, is_scpi=False,
			dummy=dummy, first_channel_num=first_channel, **kwargs)

		self.max_channels = max_ai_channels
		self.num_digital_lines = num_digital_lines

		# Configuration is authoritative in state (see module docstring), so values are stored as
		# sent rather than read back. Required, not cosmetic: see _record().
		self.blind_state_update = True

		if self.dummy:
			self.init_dummy_state()

	# ==================================================================================
	# Identity (replaces the SCPI *IDN? path)
	# ==================================================================================

	def query_id(self) -> None:
		''' Identifies the device via the driver's `_hw_device_identity()` rather than "*IDN?".
		Called by Driver.connect() during construction (skipped entirely in dummy mode, where
		connect() returns early).

		The identity should follow the *IDN? field order - "<vendor>,<model>,<serial>,<firmware>" -
		because the hardware-verification recorder reads the model and firmware out of fields 1 and
		3, and the firmware field is what makes a record go stale-firmware when it changes. '''

		try:
			idn = self._hw_device_identity()
		except Exception as e:
			self.relay.note_failure(e)
			self.error(f"Failed to read device identity. ({e})")
			self.online = False
			return

		self.id.idn_model = (idn or "").strip()
		if self.id.idn_model:
			self.online = True
			self.debug(f"Device identity: >{self.id.idn_model}<")
			if self.expected_idn and self.expected_idn.upper() in self.id.idn_model.upper():
				self.verified_hardware = True
				self.debug("Hardware verification >PASSED<")
			elif self.expected_idn:
				self.verified_hardware = False
				self.debug("Hardware verification >FAILED<", detail=f"Received: {self.id.idn_model}")
		else:
			self.online = False

	# ==================================================================================
	# Dummy support
	# ==================================================================================

	def init_dummy_state(self) -> None:
		''' Gives a dummy driver a sensible starting configuration: two enabled AI channels so the
		GUI/examples have something to show without hardware. '''

		self.id.idn_model = f"DUMMY,{type(self).__name__}"
		self.set_sample_rate(10000.0)
		self.set_samples_per_channel(1000)
		self.set_acquisition_mode(DataAcquisition.MODE_FINITE)
		self.set_ai_channel(self.first_channel, -10.0, 10.0, DataAcquisition.TERM_DIFF, enabled=True)
		if self.max_channels > 1:
			self.set_ai_channel(self.first_channel + 1, -5.0, 5.0, DataAcquisition.TERM_DIFF, enabled=True)

	def _dummy_block(self, channel:int, k0:int, n:int, dt:float) -> np.ndarray:
		''' Synthesises `n` plausible voltage samples for `channel`, starting at sample index `k0`
		with spacing `dt`: a per-channel sine wave plus a little noise, clipped to the channel's
		configured range. Indexed from k0 so consecutive stream chunks continue the same wave. '''

		t = (k0 + np.arange(n)) * dt
		freq = 10.0 * (channel - self.first_channel + 1)          # distinct tone per channel
		amp = 0.5 + 0.2 * (channel - self.first_channel)
		val = amp * np.sin(2.0 * math.pi * freq * t) + np.random.uniform(-0.02, 0.02, n)

		cs = self.state.ai_channels[channel]
		if cs is not None:
			val = np.clip(val, cs.range_min_V, cs.range_max_V)
		return val

	# ==================================================================================
	# State plumbing
	# ==================================================================================

	def _record(self, params:list, value, indices:list=None):
		''' Stores a DAQ setting or result in state, through the modify_state() choke point.

		modify_state() reads a None query_func as "this is a getter", and in dummy mode a getter
		reads the tracked value back instead of storing - which is how every DAQ setter used to
		drop its value in dummy mode. Passing a query_func marks this as a store; with
		blind_state_update set (always, for a DAQ) it is never actually called, in either mode,
		because there is no hardware register to read back from. '''

		return self.modify_state(lambda: value, params, value, indices=indices)

	def _valid_channel(self, channel:int, operation:str) -> bool:
		try:
			self.state.ai_channels.get_valid_idx(channel)
			return True
		except Exception:
			first = self.state.first_channel
			self.error(f"{operation}: no analog-input channel >{channel}<. Valid channels are {first}..{first + self.state.num_ai_channels - 1}.")
			return False

	def _valid_line(self, line:int, operation:str) -> bool:
		if isinstance(line, int) and 0 <= line < self.num_digital_lines:
			return True
		self.error(f"{operation}: no digital line >{line}<. This device has {self.num_digital_lines} line(s), numbered from 0.")
		return False

	def _run_hw(self, operation:str, primitive, *args) -> tuple:
		''' Calls one `_hw_*` primitive. The DAQ counterpart of Driver.write()/query().

		Returns:
			tuple: (success, result). On failure the error is logged, the relay records its kind,
				and the driver goes offline only if the relay judges the device unreachable - a
				rejected channel name must not take the instrument offline.
		'''

		if not self._ensure_online(operation):
			return False, None

		try:
			result = primitive(*args)
		except Exception as e:
			kind = self.relay.note_failure(e)
			self.error(f"{operation} failed ({kind.value}). ({e})")
			if kind in (RelayErrorKind.TRANSPORT, RelayErrorKind.UNKNOWN):
				self.check_online()
			return False, None

		self.relay.note_success()
		return True, result

	# ==================================================================================
	# Configuration (stored in state - nothing to read back from hardware)
	# ==================================================================================

	def set_sample_rate(self, rate_Hz:float):
		''' Sets the hardware sample-clock rate (samples/second/channel) for buffered acquisition. '''
		rate_Hz = float(rate_Hz)
		if not rate_Hz > 0:
			self.error(f"Sample rate must be positive, got >{rate_Hz}<.")
			return None
		return self._record(["sample_rate_Hz"], rate_Hz)

	def get_sample_rate(self) -> float:
		return self.state.sample_rate_Hz

	def set_samples_per_channel(self, n:int):
		''' Sets how many samples per channel a buffered acquisition captures. '''
		n = int(n)
		if n < 1:
			self.error(f"Samples per channel must be at least 1, got >{n}<.")
			return None
		return self._record(["samples_per_channel"], n)

	def get_samples_per_channel(self) -> int:
		return self.state.samples_per_channel

	def set_acquisition_mode(self, mode:str):
		''' acquire()'s timing: MODE_FINITE (hardware-clocked) or MODE_ON_DEMAND (software-timed).
		For continuous recording use start_stream(). '''
		if mode not in DataAcquisition.ACQUISITION_MODES:
			self.error(f"Unrecognised acquisition mode >{mode}<.")
			return None
		return self._record(["acquisition_mode"], mode)

	def get_acquisition_mode(self) -> str:
		return self.state.acquisition_mode

	def set_clock_source(self, source:str):
		''' Sample-clock source terminal (empty string = onboard clock). '''
		return self._record(["clock_source"], str(source or ""))

	def get_clock_source(self) -> str:
		return self.state.clock_source

	def set_ai_channel(self, channel:int, range_min_V:float=-10.0, range_max_V:float=10.0,
			terminal_config:str=None, enabled:bool=True):
		''' Convenience: configures one analog-input channel's terminal config, range and enable in
		a single call, via the single-parameter setters. '''
		if terminal_config is None:
			terminal_config = DataAcquisition.TERM_DIFF
		if not self._valid_channel(channel, "set_ai_channel"):
			return None
		self.set_ai_channel_terminal_config(channel, terminal_config)
		self.set_ai_channel_range(channel, range_min_V, range_max_V)
		self.set_ai_channel_enabled(channel, enabled)

	def set_ai_channel_enabled(self, channel:int, enabled:bool):
		if not self._valid_channel(channel, "set_ai_channel_enabled"):
			return None
		return self._record(["ai_channels", "enabled"], bool(enabled), indices=[channel])

	def set_ai_channel_terminal_config(self, channel:int, terminal_config:str):
		''' One of the TERM_* constants. Whether the hardware supports it on this channel is checked
		by the driver when a task is built, since that is device-specific. '''
		if not self._valid_channel(channel, "set_ai_channel_terminal_config"):
			return None
		if terminal_config not in DataAcquisition.TERMINAL_CONFIGS:
			self.error(f"Unrecognised terminal configuration >{terminal_config}<.")
			return None
		return self._record(["ai_channels", "terminal_config"], terminal_config, indices=[channel])

	def set_ai_channel_range(self, channel:int, range_min_V:float, range_max_V:float):
		''' Sets the expected input range. The two ends are set together because they are one
		setting to the hardware: a DAQ picks its gain from the pair. '''
		if not self._valid_channel(channel, "set_ai_channel_range"):
			return None
		range_min_V, range_max_V = float(range_min_V), float(range_max_V)
		if not range_min_V < range_max_V:
			self.error(f"Range minimum >{range_min_V}< must be below maximum >{range_max_V}<.")
			return None
		self._record(["ai_channels", "range_min_V"], range_min_V, indices=[channel])
		self._record(["ai_channels", "range_max_V"], range_max_V, indices=[channel])

	def set_ai_channel_range_pm(self, channel:int, half_range_V:float):
		''' Convenience for the common symmetric case: sets the range to +/- half_range_V. '''
		return self.set_ai_channel_range(channel, -abs(float(half_range_V)), abs(float(half_range_V)))

	def get_ai_channel(self, channel:int) -> AnalogInputChannelState:
		return self.state.ai_channels[channel]

	def _enabled_channel_specs(self, channels=None) -> list:
		''' Builds the list of channel spec dicts (channel + terminal config + range) that the
		`_hw_*` primitives consume. If `channels` is None, uses every enabled AI channel;
		otherwise uses exactly the channels listed (regardless of their enabled flag). '''

		specs = []
		rng = self.state.ai_channels.get_range() if channels is None else channels
		for ch in rng:
			if channels is not None and not self._valid_channel(ch, "channel list"):
				continue
			cs = self.state.ai_channels[ch]
			if cs is None:
				continue
			if channels is None and not cs.enabled:
				continue
			specs.append({"channel": ch, "terminal_config": cs.terminal_config,
				"range_min_V": cs.range_min_V, "range_max_V": cs.range_max_V})
		return specs

	# ==================================================================================
	# Acquisition / measurement (touches hardware via _hw_* primitives)
	# ==================================================================================

	def read_ai_single(self, channels=None) -> dict:
		''' Software-timed single-sample read of the requested (default: all enabled) AI channels.
		Returns {channel:int -> volts:float} and updates each channel's `last_value_V`. Cheap; safe
		to call interactively. Returns {} if nothing was read (no channels, or a hardware error,
		which is logged). '''

		if self._busy_streaming("read_ai_single"):
			return {}

		specs = self._enabled_channel_specs(channels)
		if len(specs) == 0:
			self.warning("read_ai_single: no channels enabled/requested.")
			return {}

		if self.dummy:
			values = [float(self._dummy_block(s["channel"], 0, 1, 0.0)[0]) for s in specs]
		else:
			ok, values = self._run_hw("read_ai_single", self._hw_read_ai_single, specs)
			if not ok:
				return {}

		result = {}
		for s, v in zip(specs, values):
			ch = s["channel"]
			v = float(v)
			self._record(["ai_channels", "last_value_V"], v, indices=[ch])
			result[ch] = v
		return result

	def acquire(self, channels=None) -> dict:
		''' Runs one buffered acquisition using the configured sample rate / samples / mode, over
		the requested (default: all enabled) AI channels. Potentially slow (seconds) - call it from
		an explicit action, never from automatic polling.

		Returns a dict with int channel keys for convenient local plotting:
			{"dt_s": float, "sample_rate_Hz": float, "t_s": [...], "channels": {ch:int -> [floats]}}
		and stores a JSON-serialisable copy (string channel keys) in state.last_acquisition.
		`dt_s` is the sample spacing the hardware actually used, which can differ from the
		configured rate (DAQ clocks coerce to an achievable divisor). Returns {} if nothing was
		acquired (no channels, or a hardware error, which is logged). '''

		if self._busy_streaming("acquire"):
			return {}

		specs = self._enabled_channel_specs(channels)
		if len(specs) == 0:
			self.warning("acquire: no channels enabled/requested.")
			return {}

		rate = self.state.sample_rate_Hz
		n = self.state.samples_per_channel
		mode = self.state.acquisition_mode
		clock = self.state.clock_source

		if self.dummy:
			dt = 1.0 / rate if rate else 0.0
			data = {s["channel"]: self._dummy_block(s["channel"], 0, n, dt) for s in specs}
		else:
			ok, out = self._run_hw("acquire", self._hw_acquire, specs, rate, n, mode, clock)
			if not ok:
				return {}
			dt, data = out

		# Plain lists from here: this result is stored in state and crosses labmesh as JSON.
		data = {ch: np.asarray(v, dtype=np.float64).tolist() for ch, v in data.items()}

		actual_rate = 1.0 / dt if dt else rate

		# Persist a serialisation-safe copy (string keys) as the tracked "last acquisition" data.
		acq_state = {"dt_s": dt, "sample_rate_Hz": actual_rate,
			"channels": {str(ch): list(v) for ch, v in data.items()}}
		self._record(["last_acquisition"], acq_state)

		# Fold the last sample of each channel into its live reading too.
		for ch, v in data.items():
			if len(v) > 0:
				self._record(["ai_channels", "last_value_V"], float(v[-1]), indices=[ch])

		n_pts = max((len(v) for v in data.values()), default=0)
		return {"dt_s": dt, "sample_rate_Hz": actual_rate, "t_s": [k * dt for k in range(n_pts)],
			"channels": {ch: list(v) for ch, v in data.items()}}

	# ==================================================================================
	# Streaming (continuous, gapless recording - see module docstring)
	# ==================================================================================

	def _busy_streaming(self, operation:str) -> bool:
		''' Analog-input snapshots can't run while a stream holds the AI subsystem. Digital I/O
		is a separate subsystem and keeps working. '''
		if self._stream is None:
			return False
		self.error(f"{operation}: analog input is busy streaming - stop_stream() first.")
		return True

	def start_stream(self, channels=None, chunk_samples:int=None) -> bool:
		''' Starts continuous, hardware-clocked sampling of the requested (default: all enabled)
		AI channels at the configured sample rate and clock source. Sampling runs from here until
		stop_stream(), whether or not anything is reading; a host-side buffer holds the samples
		until read_stream() drains them. Read faster than the board fills it, or the buffer
		overflows and the stream stops.

		Args:
			channels (list): AI channels to stream. Default: every enabled channel.
			chunk_samples (int): Samples per channel that read_stream() returns by default.
				Default: samples_per_channel.

		Returns:
			bool: True if the stream started.
		'''

		if self._stream is not None:
			self.error("start_stream: a stream is already running - stop_stream() first.")
			return False

		specs = self._enabled_channel_specs(channels)
		if len(specs) == 0:
			self.warning("start_stream: no channels enabled/requested.")
			return False

		chunk = int(chunk_samples if chunk_samples is not None else self.state.samples_per_channel)
		if chunk < 1:
			self.error(f"start_stream: chunk_samples must be at least 1, got >{chunk}<.")
			return False

		rate = self.state.sample_rate_Hz
		if self.dummy:
			dt = 1.0 / rate
		else:
			ok, dt = self._run_hw("start_stream", self._hw_start_stream, specs, rate, chunk, self.state.clock_source)
			if not ok:
				return False

		self._stream = {"specs": specs, "dt": float(dt), "chunk": chunk, "next_sample": 0, "t_start": time.perf_counter(),
			"started_at": datetime.datetime.now(datetime.timezone.utc)}
		self._record(["streaming"], True)
		self.debug(f"Stream started: {[s['channel'] for s in specs]} at {1.0 / dt:.6g} S/s, {chunk}-sample chunks.")
		return True

	def read_stream(self, n_samples:int=None, timeout_s:float=None) -> dict:
		''' Returns the next contiguous block of the running stream, blocking until it has been
		sampled.

		Args:
			n_samples (int): Samples per channel. Default: the stream's chunk size.
			timeout_s (float): How long to wait. Default: the block's duration plus 5 s.

		Returns:
			dict: {"first_sample": int, "dt_s": float, "t_s": ndarray, "channels": {ch:int -> ndarray}},
				float64 arrays of n_samples each. `first_sample` counts from the start of the
				stream and `t_s` is time since the stream started, so consecutive chunks
				concatenate into one record. Returns {} if
				no stream is running or the read failed - in which case the stream has been
				stopped (an overflowed buffer can't be resumed without a gap) and a new one must
				be started.
		'''

		st = self._stream
		if st is None:
			self.error("read_stream: no stream is running - start_stream() first.")
			return {}

		n = st["chunk"] if n_samples is None else int(n_samples)
		if n < 1:
			self.error(f"read_stream: n_samples must be at least 1, got >{n}<.")
			return {}
		if timeout_s is None:
			timeout_s = n * st["dt"] + 5.0

		first = st["next_sample"]
		dt = st["dt"]

		if self.dummy:
			# Pace like hardware: a block isn't available until it has been "sampled".
			wait = st["t_start"] + (first + n) * dt - time.perf_counter()
			if wait > 0:
				time.sleep(wait)
			data = {s["channel"]: self._dummy_block(s["channel"], first, n, dt) for s in st["specs"]}
		else:
			ok, data = self._run_hw("read_stream", self._hw_read_stream, n, timeout_s)
			if not ok:
				self.error(f"read_stream: read failed after {first} samples; the stream has been stopped. Call start_stream() to begin a new one.")
				self._end_stream(f"read failed ({self.relay.last_error_kind.value} error)")
				return {}

		st["next_sample"] = first + n
		data = {ch: np.asarray(v, dtype=np.float64) for ch, v in data.items()}

		for ch, v in data.items():
			if len(v) > 0:
				self._record(["ai_channels", "last_value_V"], float(v[-1]), indices=[ch])

		return {"first_sample": first, "dt_s": dt, "t_s": (first + np.arange(n)) * dt, "channels": data}

	def stop_stream(self) -> int:
		''' Stops the running stream and releases the hardware. Samples taken but not yet read are
		discarded.

		Returns:
			int: Samples per channel read from the stream, or 0 if none was running.
		'''

		if self._stream is None:
			self.warning("stop_stream: no stream is running.")
			return 0

		total = self._stream["next_sample"]
		self._end_stream("stopped")
		self.debug(f"Stream stopped after {total} samples per channel.")
		return total

	def _end_stream(self, reason:str) -> None:
		''' Tears the stream down unconditionally, recording why in `last_stream_end`. Not gated on
		being online, unlike _run_hw(): a stream whose device dropped off still holds a task that
		must be released. '''

		self.last_stream_end = {"reason": reason, "samples": self._stream["next_sample"]}
		if not self.dummy:
			try:
				self._hw_stop_stream()
			except Exception as e:
				self.warning(f"Stopping the stream raised; its hardware may still be reserved. ({e})")
		self._stream = None
		self._record(["streaming"], False)

	def is_streaming(self) -> bool:
		return self._stream is not None

	def stream_info(self) -> dict:
		''' Describes the running stream - what a recording's metadata needs - or None.

		Returns:
			dict: {"started_at": aware UTC datetime, "dt_s", "sample_rate_Hz" (the rate the
				hardware really uses), "chunk_samples", "samples_read", "channels":
				{ch:int -> {"terminal_config", "range_min_V", "range_max_V"}}}.
		'''

		st = self._stream
		if st is None:
			return None

		return {"started_at": st["started_at"], "dt_s": st["dt"], "sample_rate_Hz": 1.0 / st["dt"],
			"chunk_samples": st["chunk"], "samples_read": st["next_sample"],
			"channels": {s["channel"]: {k: s[k] for k in ("terminal_config", "range_min_V", "range_max_V")} for s in st["specs"]}}

	def stream(self, callback, duration_s:float=None, max_chunks:int=None, channels=None, chunk_samples:int=None) -> int:
		''' Records continuously, handing each chunk to `callback(chunk)` as it arrives - the
		"record until I say stop" loop. Blocks until `callback` returns False, `duration_s` of
		signal has been recorded, `max_chunks` chunks have been read, a read fails, or the caller
		interrupts (Ctrl+C). The stream is stopped however the loop ends.

		Keep `callback` quicker than one chunk's duration on average, or the buffer eventually
		overflows. Run this in a thread if the caller has other work to do meanwhile.

		Args:
			callback (callable): chunk dict (see read_stream()) -> anything; False stops.
			duration_s (float): Stop once this much signal time has been recorded.
			max_chunks (int): Stop after this many chunks.
			channels, chunk_samples: As for start_stream().

		Returns:
			int: Samples per channel recorded.
		'''

		if not self.start_stream(channels=channels, chunk_samples=chunk_samples):
			return 0

		chunks = 0
		reason = "stopped"
		try:
			while True:
				chunk = self.read_stream()
				if not chunk:
					break          # read_stream() has already ended the stream, with its reason
				chunks += 1
				if callback(chunk) is False:
					reason = "callback returned False"
					break
				if max_chunks is not None and chunks >= max_chunks:
					reason = f"max_chunks={max_chunks} reached"
					break
				if duration_s is not None and (chunk["first_sample"] + len(chunk["t_s"])) * chunk["dt_s"] >= duration_s:
					reason = f"duration_s={duration_s:g} reached"
					break
		except KeyboardInterrupt:
			reason = "interrupted"
			raise
		except Exception as e:
			reason = f"callback raised {type(e).__name__}: {e}"
			raise
		finally:
			if self._stream is not None:
				self._end_stream(reason)

		return self.last_stream_end["samples"]

	def close(self) -> None:
		''' Stops any running stream before closing, so the hardware isn't left reserved. '''
		if self._stream is not None:
			self._end_stream("driver closed")
		super().close()

	def read_digital_line(self, line:int):
		''' Reads a single digital input line. Updates state.digital_in. Returns None on error. '''
		if not self._valid_line(line, "read_digital_line"):
			return None
		if self.dummy:
			val = bool(self.state.digital_out.get(str(line), False))  # loop-back so dummy is deterministic
		else:
			ok, val = self._run_hw("read_digital_line", self._hw_read_digital_line, line)
			if not ok:
				return None
			val = bool(val)
		di = dict(self.state.digital_in)
		di[str(line)] = val
		self._record(["digital_in"], di)
		return val

	def write_digital_line(self, line:int, value:bool):
		''' Writes a single digital output line. Updates state.digital_out only if the write
		succeeded. Returns the value written, or None on error. '''
		if not self._valid_line(line, "write_digital_line"):
			return None
		value = bool(value)
		if not self.dummy:
			ok, _ = self._run_hw("write_digital_line", self._hw_write_digital_line, line, value)
			if not ok:
				return None
		do = dict(self.state.digital_out)
		do[str(line)] = value
		self._record(["digital_out"], do)
		return value

	# ==================================================================================
	# Driver framework hooks
	# ==================================================================================

	def refresh_state(self):
		''' No-op for a DAQ: configuration is authoritative in state (the hardware holds no
		persistent settings to re-read). Present so poll() works and the GUI keeps getting fresh
		state snapshots. '''
		pass

	def apply_state(self):
		''' Nothing to push: a DAQ's configuration is consumed when a task is built at
		acquire()/read time, not written to persistent hardware registers. '''
		self.lowdebug("apply_state(): DAQ configuration is applied at acquisition time; nothing to push now.")

	def refresh_data(self):
		''' Best-effort single-shot read of all enabled channels. Skipped while streaming: the
		stream's consumer keeps each channel's last_value_V current, and a poll must never steal
		samples from a recording. '''
		if self._stream is not None:
			return
		try:
			self.read_ai_single()
		except Exception as e:
			self.lowdebug(f"refresh_data() skipped: {e}")

	# ==================================================================================
	# Hardware primitives - a concrete driver implements ONLY these. They may raise; the
	# category calls them through _run_hw(), which handles the failure.
	# ==================================================================================

	@abstractmethod
	def _hw_read_ai_single(self, specs:list) -> list:
		''' Return one voltage sample per channel spec, in the same order as `specs`.
		`specs` is a list of {"channel", "terminal_config", "range_min_V", "range_max_V"}. '''
		...

	@abstractmethod
	def _hw_acquire(self, specs:list, sample_rate:float, n_samples:int, mode:str, clock_source:str) -> tuple:
		''' Run one snapshot of `n_samples` per channel, then release the hardware. Return
		(dt_s, {channel:int -> [floats]}), where dt_s is the spacing the hardware really used.
		MODE_ON_DEMAND has no sample clock: software-timed reads, with dt_s the mean interval. '''
		...

	@abstractmethod
	def _hw_start_stream(self, specs:list, sample_rate:float, chunk_samples:int, clock_source:str) -> float:
		''' Start continuous hardware-clocked sampling of `specs` into a host buffer comfortably
		larger than one chunk, running until _hw_stop_stream(). Return the sample spacing the
		hardware really used (dt_s). Release anything acquired if starting fails. '''
		...

	@abstractmethod
	def _hw_read_stream(self, n_samples:int, timeout_s:float) -> dict:
		''' Return the next `n_samples` per channel of the running stream, contiguous with the
		previous read, as {channel:int -> array-like of floats} (numpy preferred). Block until available; raise on timeout or
		overflow - never return a short or gapped block. '''
		...

	@abstractmethod
	def _hw_stop_stream(self) -> None:
		''' Stop the stream and release its hardware. Must be safe after a failed read. '''
		...

	@abstractmethod
	def _hw_read_digital_line(self, line:int) -> bool:
		''' Return the state of a single digital input line. '''
		...

	@abstractmethod
	def _hw_write_digital_line(self, line:int, value:bool) -> None:
		''' Drive a single digital output line. '''
		...

	@abstractmethod
	def _hw_device_identity(self) -> str:
		''' Return the device identity in *IDN? field order,
		"<vendor>,<model>,<serial>,<firmware/driver version>" - used by query_id() in place of
		"*IDN?". See query_id() for why the field order matters. '''
		...
