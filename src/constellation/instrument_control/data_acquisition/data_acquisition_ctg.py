''' Category class for multifunction data-acquisition (DAQ) devices.

Unlike the SCPI instrument categories, a DAQ (e.g. an NI M-series multifunction card) is NOT a
text/request-response instrument - it has no persistent, queryable configuration registers. A
measurement is defined by building a *task* (a set of channels + timing), running it, and reading
back a block of samples; between tasks the hardware holds essentially no state. Two consequences
shape this category:

  1. Configuration (sample rate, per-channel range/terminal-config/enable, ...) is authoritative in
     the driver's InstrumentState, not on the hardware. So the set_* methods here are plain blind
     state updates (query_func=None) - there is nothing to read back to verify, and refresh_state()
     has nothing to re-read.
  2. Everything that actually touches hardware is funnelled through a small set of abstract
     `_hw_*` primitives. A concrete driver only implements those (translating them to nidaqmx, or
     comedi, or a reverse-engineered USB protocol); all orchestration, state tracking and dummy
     simulation live here, mirroring how the SCPI categories keep everything but the SCPI strings
     out of the driver file.

Because a DAQ is not SCPI, drivers in this category are constructed with is_scpi=False, and this
category overrides query_id() to identify the device via `_hw_device_identity()` rather than
"*IDN?".
'''

import math
import random

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
		"ai_channels", "last_acquisition")

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



class DataAcquisition(Driver):
	''' Abstract DAQ driver. Concrete drivers implement only the `_hw_*` primitives (see bottom of
	class). Everything user-facing - configuration, single-point reads, buffered acquisition,
	digital I/O, dummy simulation and state tracking - is implemented here. '''

	# --- Terminal (input) configurations ---
	TERM_DIFF = "differential"
	TERM_RSE = "rse"
	TERM_NRSE = "nrse"
	TERM_PSEUDODIFF = "pseudodifferential"

	# --- Acquisition modes ---
	MODE_FINITE = "finite"          # Fixed number of hardware-timed samples, then stop.
	MODE_CONTINUOUS = "continuous"  # Free-running hardware-timed acquisition.
	MODE_ON_DEMAND = "on_demand"    # Software-timed, one sample per read (no sample clock).

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="",
			max_ai_channels:int=16, num_digital_lines:int=0, dummy:bool=False, first_channel:int=0, **kwargs):

		_state = DataAcquisitionState(first_channel, max_ai_channels, num_digital_lines=num_digital_lines, log=log)

		# A DAQ is not a SCPI instrument - is_scpi=False disables the base class's *IDN?/*RST/write
		# machinery, which would be meaningless here.
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, is_scpi=False,
			dummy=dummy, first_channel_num=first_channel, **kwargs)

		self.max_channels = max_ai_channels
		self.num_digital_lines = num_digital_lines

		if self.dummy:
			self.init_dummy_state()

	# ==================================================================================
	# Identity (replaces the SCPI *IDN? path)
	# ==================================================================================

	def query_id(self) -> None:
		''' Identifies the device via the driver's `_hw_device_identity()` rather than "*IDN?".
		Called by Driver.connect() during construction (skipped entirely in dummy mode, where
		connect() returns early). '''

		try:
			idn = self._hw_device_identity()
		except Exception as e:
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

	def _dummy_sample(self, channel:int, k:int=0, dt:float=0.0) -> float:
		''' Synthesises a plausible voltage sample for `channel` at sample index `k` (spacing `dt`):
		a per-channel sine wave plus a little noise, clipped to the channel's configured range. '''

		t = k * dt
		freq = 10.0 * (channel - self.first_channel + 1)          # distinct tone per channel
		amp = 0.5 + 0.2 * (channel - self.first_channel)
		val = amp * math.sin(2.0 * math.pi * freq * t) + random.uniform(-0.02, 0.02)

		cs = self.state.ai_channels[channel]
		if cs is not None:
			val = max(cs.range_min_V, min(cs.range_max_V, val))
		return val

	# ==================================================================================
	# Configuration (blind state updates - nothing to read back from hardware)
	# ==================================================================================

	def set_sample_rate(self, rate_Hz:float):
		''' Sets the hardware sample-clock rate (samples/second/channel) for buffered acquisition. '''
		return self.modify_state(None, ["sample_rate_Hz"], float(rate_Hz))

	def get_sample_rate(self) -> float:
		return self.state.sample_rate_Hz

	def set_samples_per_channel(self, n:int):
		''' Sets how many samples per channel a finite buffered acquisition captures. '''
		return self.modify_state(None, ["samples_per_channel"], int(n))

	def get_samples_per_channel(self) -> int:
		return self.state.samples_per_channel

	def set_acquisition_mode(self, mode:str):
		''' One of MODE_FINITE / MODE_CONTINUOUS / MODE_ON_DEMAND. '''
		if mode not in (DataAcquisition.MODE_FINITE, DataAcquisition.MODE_CONTINUOUS, DataAcquisition.MODE_ON_DEMAND):
			self.error(f"Unrecognised acquisition mode >{mode}<.")
			return None
		return self.modify_state(None, ["acquisition_mode"], mode)

	def get_acquisition_mode(self) -> str:
		return self.state.acquisition_mode

	def set_clock_source(self, source:str):
		''' Sample-clock source terminal (empty string = onboard clock). '''
		return self.modify_state(None, ["clock_source"], source)

	def set_ai_channel(self, channel:int, range_min_V:float=-10.0, range_max_V:float=10.0,
			terminal_config:str=None, enabled:bool=True):
		''' Configures one analog-input channel in a single call. '''
		if terminal_config is None:
			terminal_config = DataAcquisition.TERM_DIFF
		self.modify_state(None, ["ai_channels", "terminal_config"], terminal_config, indices=[channel])
		self.modify_state(None, ["ai_channels", "range_min_V"], float(range_min_V), indices=[channel])
		self.modify_state(None, ["ai_channels", "range_max_V"], float(range_max_V), indices=[channel])
		self.modify_state(None, ["ai_channels", "enabled"], bool(enabled), indices=[channel])

	def set_ai_channel_enabled(self, channel:int, enabled:bool):
		return self.modify_state(None, ["ai_channels", "enabled"], bool(enabled), indices=[channel])

	def set_ai_channel_terminal_config(self, channel:int, terminal_config:str):
		return self.modify_state(None, ["ai_channels", "terminal_config"], terminal_config, indices=[channel])

	def set_ai_channel_range(self, channel:int, range_min_V:float, range_max_V:float):
		self.modify_state(None, ["ai_channels", "range_min_V"], float(range_min_V), indices=[channel])
		self.modify_state(None, ["ai_channels", "range_max_V"], float(range_max_V), indices=[channel])

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
		to call interactively. '''

		specs = self._enabled_channel_specs(channels)
		if len(specs) == 0:
			self.warning("read_ai_single: no channels enabled/requested.")
			return {}

		if self.dummy:
			values = [self._dummy_sample(s["channel"]) for s in specs]
		else:
			values = self._hw_read_ai_single(specs)

		result = {}
		for s, v in zip(specs, values):
			ch = s["channel"]
			v = float(v)
			self.modify_state(None, ["ai_channels", "last_value_V"], v, indices=[ch])
			result[ch] = v
		return result

	def acquire(self, channels=None) -> dict:
		''' Runs one hardware-timed buffered acquisition using the configured sample rate / samples
		/ mode, over the requested (default: all enabled) AI channels. Potentially slow (seconds) -
		call it from an explicit action, never from automatic polling.

		Returns a dict with int channel keys for convenient local plotting:
			{"dt_s": float, "sample_rate_Hz": float, "t_s": [...], "channels": {ch:int -> [floats]}}
		and stores a JSON-serialisable copy (string channel keys) in state.last_acquisition. '''

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
			data = {s["channel"]: [self._dummy_sample(s["channel"], k, dt) for k in range(n)] for s in specs}
		else:
			dt, data = self._hw_acquire(specs, rate, n, mode, clock)

		# Persist a serialisation-safe copy (string keys) as the tracked "last acquisition" data.
		acq_state = {"dt_s": dt, "sample_rate_Hz": rate,
			"channels": {str(ch): list(v) for ch, v in data.items()}}
		self.modify_state(None, ["last_acquisition"], acq_state)

		# Fold the last sample of each channel into its live reading too.
		for ch, v in data.items():
			if len(v) > 0:
				self.modify_state(None, ["ai_channels", "last_value_V"], float(v[-1]), indices=[ch])

		n_pts = max((len(v) for v in data.values()), default=0)
		return {"dt_s": dt, "sample_rate_Hz": rate, "t_s": [k * dt for k in range(n_pts)],
			"channels": {ch: list(v) for ch, v in data.items()}}

	def read_digital_line(self, line:int) -> bool:
		''' Reads a single digital input line. Updates state.digital_in. '''
		if self.dummy:
			val = bool(self.state.digital_out.get(str(line), False))  # loop-back so dummy is deterministic
		else:
			val = bool(self._hw_read_digital_line(line))
		di = dict(self.state.digital_in)
		di[str(line)] = val
		self.modify_state(None, ["digital_in"], di)
		return val

	def write_digital_line(self, line:int, value:bool) -> bool:
		''' Writes a single digital output line. Updates state.digital_out. '''
		value = bool(value)
		if not self.dummy:
			self._hw_write_digital_line(line, value)
		do = dict(self.state.digital_out)
		do[str(line)] = value
		self.modify_state(None, ["digital_out"], do)
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
		''' Best-effort single-shot read of all enabled channels. '''
		try:
			self.read_ai_single()
		except Exception as e:
			self.lowdebug(f"refresh_data() skipped: {e}")

	# ==================================================================================
	# Hardware primitives - a concrete driver implements ONLY these.
	# ==================================================================================

	@abstractmethod
	def _hw_read_ai_single(self, specs:list) -> list:
		''' Return one voltage sample per channel spec, in the same order as `specs`.
		`specs` is a list of {"channel", "terminal_config", "range_min_V", "range_max_V"}. '''
		...

	@abstractmethod
	def _hw_acquire(self, specs:list, sample_rate:float, n_samples:int, mode:str, clock_source:str) -> tuple:
		''' Run a buffered, hardware-timed acquisition. Return (dt_s, {channel:int -> [floats]}). '''
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
		''' Return a human-readable identity string (e.g. "National Instruments,USB-6210,0x...") -
		used by query_id() in place of "*IDN?". '''
		...
