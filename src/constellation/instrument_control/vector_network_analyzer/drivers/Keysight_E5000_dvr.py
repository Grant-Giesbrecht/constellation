''' Driver for the Agilent / Keysight E5000-series ENA network analyzers.

Written against the E5071C (2 or 4 port, 9 kHz - 20 GHz). The E5070B, E5071B and E5072A share
its SCPI command set and are expected to work, but only the E5071C was used as the reference. The
E5061B/E5063A are a different, smaller ENA line and are NOT covered.

	E5071C Programmer's Guide / SCPI Command Reference (Keysight part no. E5071-90xxx).

Where this differs from the R&S ZVA driver it was started from:

- **Traces are numbered, not named.** A ZVA trace is created with a name ('Trc1') and addressed
  by it. On the E5071C a channel just has N traces, `CALC<ch>:PAR<n>`, and nothing to name them
  with. The category API addresses traces by string, so this driver keeps the name -> (channel,
  trace number) table itself (`_trace_addr`). Names given to `add_trace()` are remembered for the
  life of the driver object; traces found on the instrument by `refresh_channels_and_traces()`
  that this driver didn't create are called `Ch<ch>_Tr<n>`.
- **Traces can't be deleted individually**, and a channel always has at least one. `clear_traces()`
  therefore resets every channel to a single trace.
- **One source power per channel.** There is no per-port power on this instrument, so
  `set_power`/`get_power` take only a channel.
- **Binary transfers go through `Driver.query_binary()`** (and so work over a networked relay),
  not through `relay.inst`.

None of the SCPI below has been checked against hardware yet. In particular the way channel
existence is probed in `refresh_channels_and_traces()` (see there) is a guess.
'''

from constellation.base import *
from constellation.instrument_control.vector_network_analyzer.vector_network_analyzer_ctg import *

# Category constant <-> the token `CALC<ch>:SEL:FORM` uses. The reverse lookup is generated from
# the same table so the setter and the getter cannot drift into different vocabularies. Formats
# the category has no constant for (group delay, the SLIN/SLOG/SCOM Smith variants, PLIN/PLOG)
# read back as FORM_OTHER.
FORMAT_CODES = {
	BasicVectorNetworkAnalyzerCtg.FORM_LOG_MAG: "MLOG",
	BasicVectorNetworkAnalyzerCtg.FORM_PHASE: "PHAS",
	BasicVectorNetworkAnalyzerCtg.FORM_SMITH: "SMIT",
	BasicVectorNetworkAnalyzerCtg.FORM_POLAR: "POL",
	BasicVectorNetworkAnalyzerCtg.FORM_SWR: "SWR",
	BasicVectorNetworkAnalyzerCtg.FORM_LIN_MAG: "MLIN",
	BasicVectorNetworkAnalyzerCtg.FORM_REAL: "REAL",
	BasicVectorNetworkAnalyzerCtg.FORM_IMAG: "IMAG",
	BasicVectorNetworkAnalyzerCtg.FORM_SMITH_INV: "SADM", # Smith chart, admittance
	BasicVectorNetworkAnalyzerCtg.FORM_PHASE_UW: "UPH",
}
FORMAT_CODES_INV = {code: fmt for fmt, code in FORMAT_CODES.items()}

# Only the four S-parameters the category defines. A 4-port E5071C can also measure S13...S44 and
# the like, which the category has no constants for.
MEASUREMENT_CODES = {
	BasicVectorNetworkAnalyzerCtg.MEAS_S11: "S11",
	BasicVectorNetworkAnalyzerCtg.MEAS_S12: "S12",
	BasicVectorNetworkAnalyzerCtg.MEAS_S21: "S21",
	BasicVectorNetworkAnalyzerCtg.MEAS_S22: "S22",
}
MEASUREMENT_CODES_INV = {code: meas for meas, code in MEASUREMENT_CODES.items()}

class KeysightE5000(BasicVectorNetworkAnalyzerCtg):

	MAX_CHANNELS = 9
	MAX_TRACES_PER_CHANNEL = 9

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="E507", **kwargs):
		''' Args:
			expected_idn (str): Default "E507" matches the E5070B/E5071B/E5071C/E5072A. It is the
				model prefix and not the vendor because the same instrument answers "Agilent
				Technologies,E5071C,..." or "Keysight Technologies,E5071C,..." depending on its
				firmware vintage. Pass your exact model for a stricter check.

		`max_channels`/`max_traces` are not parameters - they are properties of the instrument.
		'''

		# name -> (channel, trace number on that channel). Declared before super().__init__()
		# because dummy mode runs init_dummy_state() from inside it.
		self._trace_addr = {}

		super().__init__(address, log, relay=relay, expected_idn=expected_idn,
			max_channels=self.MAX_CHANNELS, max_traces=self.MAX_CHANNELS*self.MAX_TRACES_PER_CHANNEL, **kwargs)

	def preset(self) -> None:
		''' Presets the instrument. Uses SYST:PRES, which on this instrument also resets the
		display and channel layout; `*RST` does not. '''
		self.write("SYST:PRES")
		self._trace_addr.clear()

	def _get_trace_idx(self, trace_name:str) -> int:
		''' Returns the state index for the requested trace, or None if not found. '''

		for idx, tr in self.state.traces.populated_items():
			if tr.id_str == trace_name:
				return idx

		return None

	def valid_trace_name(self, name:str) -> bool:
		''' The instrument never sees the name, so the only rule is that it can identify a trace:
		a non-empty string. '''

		return isinstance(name, str) and name.strip() != ""

	def _from_format_code(self, code:str) -> str:
		''' Hardware format token -> category format constant (FORM_OTHER if unrecognized). '''

		_code = code.strip().strip('"').upper()
		if _code not in FORMAT_CODES_INV:
			self.debug(f"Format code >{_code}< has no category constant, using FORM_OTHER.")
			return BasicVectorNetworkAnalyzerCtg.FORM_OTHER
		return FORMAT_CODES_INV[_code]

	def _from_meas_code(self, code:str) -> str:
		''' Hardware measurement token -> category measurement constant. Returns None for
		measurements the category can't express (S13, ...), rather than mislabeling them. '''

		_code = code.strip().strip('"').upper()
		if _code not in MEASUREMENT_CODES_INV:
			self.debug(f"Measurement code >{_code}< has no category constant.")
			return None
		return MEASUREMENT_CODES_INV[_code]

	def _parse_int(self, response:str):
		''' Integer out of a reply, tolerating the "+1.0E+0" form. None if unparsable. '''
		try:
			return int(float(response.strip()))
		except (ValueError, AttributeError):
			return None

	def _address_of(self, trace_name:str):
		''' (channel, trace number) for a trace name, or None (with an error logged). '''

		if trace_name not in self._trace_addr:
			self.error(f"Trace >'{trace_name}'< does not exist!")
			return None
		return self._trace_addr[trace_name]

	def refresh_channels_and_traces(self):
		''' Rebuilds channel and trace state from the instrument. Channels can be
		non-consecutive, so Ch1 and Ch3 could exist without Ch2.

		A channel is taken to exist if `CALC<ch>:PAR:COUN?` answers with a positive count. This is
		UNVERIFIED: if the instrument instead answers an absent channel with a count of zero or
		an error, both read as "absent" here, but an error may also leave an entry in the
		instrument's error queue.
		'''

		self.state.channels.clear()
		self.state.traces.clear()
		trc_idx = self.first_trace

		known_names = {addr: name for name, addr in self._trace_addr.items()}
		self._trace_addr = {}

		for ch in range(self.first_channel, self.first_channel + self.max_channels):

			count = self._parse_int(self.query(f"CALC{ch}:PAR:COUN?"))
			self.lowdebug(f"Read channel #{ch} trace count:{count}")
			if not count:
				continue

			self.state.channels[ch] = VNAChannelState(log=self.log)
			self.state.channels[ch].enabled = True

			for n in range(1, count+1):

				name = known_names.get((ch, n), f"Ch{ch}_Tr{n}")
				self._trace_addr[name] = (ch, n)

				trace = VNATraceState(log=self.log)
				trace.enabled = True
				trace.channel = ch
				trace.id_str = name
				trace.measurement = self._from_meas_code(self.query(f"CALC{ch}:PAR{n}:DEF?"))

				# The format is only queryable for the selected trace
				self.write(f"CALC{ch}:PAR{n}:SEL")
				trace.format = self._from_format_code(self.query(f"CALC{ch}:SEL:FORM?"))

				self.state.traces[trc_idx] = trace
				trc_idx += 1

			self.get_freq_start(ch)
			self.get_freq_end(ch)
			self.get_res_bandwidth(ch)
			self.get_num_points(ch)
			self.get_power(ch)
			self.get_cal_enabled(ch)

	@superreturn
	def set_freq_start(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STAR {f_Hz}")

	@superreturn
	def get_freq_start(self, channel:int=1):
		return float(self.query(f"SENS{channel}:FREQ:STAR?"))

	@superreturn
	def set_freq_end(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STOP {f_Hz}")

	@superreturn
	def get_freq_end(self, channel:int=1):
		return float(self.query(f"SENS{channel}:FREQ:STOP?"))

	@superreturn
	def set_power(self, p_dBm:float, channel:int=1):
		self.write(f"SOUR{channel}:POW {p_dBm}")

	@superreturn
	def get_power(self, channel:int=1):
		return float(self.query(f"SOUR{channel}:POW?"))

	@superreturn
	def set_num_points(self, points:int, channel:int=1):
		self.write(f"SENS{channel}:SWE:POIN {int(points)}")

	@superreturn
	def get_num_points(self, channel:int=1):
		return self._parse_int(self.query(f"SENS{channel}:SWE:POIN?"))

	@superreturn
	def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:BAND {rbw_Hz}")

	@superreturn
	def get_res_bandwidth(self, channel:int=1):
		return float(self.query(f"SENS{channel}:BAND?"))

	@superreturn
	def set_cal_enabled(self, enable:bool, channel:int=1):
		self.write(f"SENS{channel}:CORR:STAT {bool_to_ONOFF(enable)}")

	@superreturn
	def get_cal_enabled(self, channel:int=1):
		return str_to_bool(self.query(f"SENS{channel}:CORR:STAT?"))

	@superreturn
	def set_rf_enable(self, enable:bool):
		self.write(f"OUTP {bool_to_ONOFF(enable)}")

	@superreturn
	def get_rf_enable(self):
		return str_to_bool(self.query("OUTP?"))

	@superreturn
	def set_rf_power(self, power_dBm:float, channel:int=1):
		self.write(f"SOUR{channel}:POW {power_dBm}")

	@superreturn
	def get_rf_power(self, channel:int=1):
		return float(self.query(f"SOUR{channel}:POW?"))

	def clear_traces(self):
		''' Resets every existing channel to a single trace. The instrument has no way to delete
		the last trace of a channel, so this cannot leave a channel empty - the survivor is
		picked up by refresh_channels_and_traces() under its default name. '''

		if self.dummy:
			self.state.traces.clear()
			self._trace_addr.clear()
			return

		for ch, _ in self.state.channels.populated_items():
			self.write(f"CALC{ch}:PAR:COUN 1")

		self.refresh_channels_and_traces()

	def add_trace(self, channel:int, trace_name:str, measurement:str) -> bool:
		''' Adds a trace measuring `measurement` to an existing channel. Returns True on success.
		Creating a new channel is not supported. '''

		if measurement not in MEASUREMENT_CODES:
			self.error(f"Unrecognized measurement >{measurement}<. Aborting add_trace.")
			return False

		if trace_name in self._trace_addr:
			self.error(f"Trace name >'{trace_name}'< already exists. Aborting add_trace.")
			return False

		if not self.valid_trace_name(trace_name):
			self.error(f"Trace name >'{trace_name}'< invalid. Aborting add_trace.")
			return False

		if not self.state.channels.idx_is_populated(channel):
			self.error(f"Channel {channel} does not exist (or state is stale - try refresh_channels_and_traces()). Aborting add_trace.")
			return False

		# A channel starts with one trace, so a lone default trace is reused for the first
		# add_trace() after a preset/clear; any later one grows the channel.
		on_channel = [n for ch, n in self._trace_addr.values() if ch == channel]
		n = max(on_channel, default=0) + 1
		if n > self.MAX_TRACES_PER_CHANNEL:
			self.error(f"Channel {channel} already has {self.MAX_TRACES_PER_CHANNEL} traces. Aborting add_trace.")
			return False

		self.write(f"CALC{channel}:PAR:COUN {n}")
		self.write(f"CALC{channel}:PAR{n}:DEF {MEASUREMENT_CODES[measurement]}")

		self._trace_addr[trace_name] = (channel, n)

		# Keep state in step without a full refresh
		trace = VNATraceState(log=self.log)
		trace.enabled = True
		trace.channel = channel
		trace.id_str = trace_name
		trace.measurement = measurement
		trace.format = BasicVectorNetworkAnalyzerCtg.FORM_LOG_MAG # Instrument default for a new trace

		slot = max([idx for idx, _ in self.state.traces.populated_items()], default=self.first_trace-1) + 1
		self.state.traces[slot] = trace

		return True

	@superreturn
	def get_trace_data(self, trace_name:str):
		''' Returns the complex (corrected, unformatted) S-parameter data of a trace.

		Channel Data:
			* x: X data list, frequency (Hz) (float)
			* y: Y data list, complex S-parameter (complex)
			* x_units: Units of x-axis
			* y_units: Units of y-axis

		Returns None if the trace doesn't exist or the transfer fails.
		'''

		addr = self._address_of(trace_name)
		if addr is None:
			return None
		channel, n = addr

		self.write(f"CALC{channel}:PAR{n}:SEL")

		# 64-bit floats, little-endian: the byte order query_binary()'s pyvisa default expects
		self.write("FORM:DATA REAL")
		self.write("FORM:BORD SWAP")

		# SDAT is real/imaginary pairs, one per point, regardless of the display format
		raw = self.query_binary(f"CALC{channel}:SEL:DATA:SDAT?", datatype='d')
		if not raw:
			self.error(f"No data returned for trace >'{trace_name}'<.")
			return None

		y_data = np.array(raw[0::2]) + 1j * np.array(raw[1::2])

		# The stimulus axis comes from the instrument so that log and segment sweeps are right.
		# Fall back to linear only if that read fails.
		freqs_Hz = self.query_binary(f"SENS{channel}:FREQ:DATA?", datatype='d')
		if len(freqs_Hz) != len(y_data):
			self.warning(f"Stimulus axis has {len(freqs_Hz)} points for {len(y_data)} data points, assuming a linear sweep.")
			freqs_Hz = list(np.linspace(self.get_freq_start(channel), self.get_freq_end(channel), len(y_data)))

		return {'x': list(freqs_Hz), 'y': y_data, 'x_units': 'Hz', 'y_units': 'S-parameter, complex, unitless'}

	def refresh_data(self):
		pass
