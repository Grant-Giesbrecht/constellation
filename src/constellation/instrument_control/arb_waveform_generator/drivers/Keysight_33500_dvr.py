''' Keysight (formerly Agilent) Trueform waveform generators.

Covers the **33500B** and **33600A** series - 33509B/33510B/33511B/33512B/33519B/33520B/33521B/
33522B and 33611A/33612A/33621A/33622A - which share this command set. Single- and dual-channel
models both exist in each series; pass `max_channels` accordingly (default 2).

	https://www.keysight.com/gb/en/assets/9018-03290/user-manuals/9018-03290.pdf
'''

from constellation.base import *
from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_ctg import *

# Category constant <-> the `SOUR<n>:FUNC` token. One table with a generated reverse lookup, so
# the setter and getter cannot drift into different vocabularies.
#
# NOTE: the instrument also supports TRI (triangle), which the category has no constant for. A
# triangle is a ramp at 50% symmetry, but conflating them would make get_waveform() report
# something set_waveform() cannot reproduce, so TRI deliberately maps to nothing and is reported
# as an unrecognized waveform instead.
FUNC_CODES = {
	ArbitraryWaveformGenerator.WAVE_SINE: "SIN",
	ArbitraryWaveformGenerator.WAVE_SQUARE: "SQU",
	ArbitraryWaveformGenerator.WAVE_RAMP: "RAMP",
	ArbitraryWaveformGenerator.WAVE_PULSE: "PULS",
	ArbitraryWaveformGenerator.WAVE_NOISE: "NOIS",
	ArbitraryWaveformGenerator.WAVE_ARB: "ARB",
	ArbitraryWaveformGenerator.WAVE_DC: "DC",
}
FUNC_CODES_INV = {code: wave for wave, code in FUNC_CODES.items()}

POLARITY_CODES = {
	ArbitraryWaveformGenerator.POLARITY_NORMAL: "NORM",
	ArbitraryWaveformGenerator.POLARITY_INVERTED: "INV",
}
POLARITY_CODES_INV = {code: polarity for polarity, code in POLARITY_CODES.items()}

# What `OUTP<n>:LOAD?` reports when the output is set to high impedance. The instrument accepts the
# word INF on the way in but answers with this number on the way out, so the two directions are not
# symmetric and the readback has to be recognized by magnitude.
HIGH_Z_READBACK = 9.9e37
HIGH_Z_THRESHOLD = 1e37

# Waveform -> the subsystem its duty cycle lives under. Unlike the Siglent, which has one DUTY
# keyword for every waveform, this instrument keeps a separate duty cycle per function, so the
# command depends on what the channel is currently generating.
DUTY_SUBSYSTEM = {
	ArbitraryWaveformGenerator.WAVE_SQUARE: "SQU",
	ArbitraryWaveformGenerator.WAVE_PULSE: "PULS",
}

class Keysight33500(ArbitraryWaveformGenerator):
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="Technologies,33", **kwargs):
		''' Args:
			expected_idn (str): Default matches both families - a 33500B answers
				"Agilent Technologies,33522B,..." and a 33600A answers
				"Keysight Technologies,33622A,...", so the vendor word differs but
				"Technologies,33" is common to both. Override it with your exact model if you want
				a stricter check.
		'''
		super().__init__(address, log, relay=relay, expected_idn=expected_idn, **kwargs)
	
	def _parse_number(self, response_str:str):
		''' Floats out of a reply.
		
		This instrument answers numerics in bare scientific notation with an explicit sign
		("+1.00000000000000E+03") and no unit suffix, which float() handles directly - unlike the
		Siglent, whose replies carry units and need a suffix-aware parser.
		'''
		
		if not response_str:
			return None
		
		try:
			return float(response_str.strip())
		except ValueError:
			self.error(f"Could not read a number from >{response_str.strip()}<.")
			return None
	
	@superreturn
	def set_waveform(self, channel:int, wave:str):
		
		code = FUNC_CODES.get(wave)
		if code is None:
			self.error(f"Failed to recognize code >{wave}<.")
			return
		
		self.write(f"SOUR{channel}:FUNC {code}")
	
	@superreturn
	def get_waveform(self, channel:int):
		
		response_str = self.query(f"SOUR{channel}:FUNC?")
		if not response_str:
			return None
		
		code = response_str.strip().upper()
		
		wave = FUNC_CODES_INV.get(code)
		if wave is None:
			# TRI, and anything else the category has no word for. Reporting None is honest;
			# guessing at the nearest constant would make apply_state() write the wrong function.
			self.warning(f"Instrument reports waveform >{code}<, which has no category equivalent.")
		
		return wave
	
	@superreturn
	def set_frequency(self, channel:int, freq_hz:float):
		self.write(f"SOUR{channel}:FREQ {freq_hz}")
	
	@superreturn
	def get_frequency(self, channel:int):
		return self._parse_number(self.query(f"SOUR{channel}:FREQ?"))
	
	@superreturn
	def set_amplitude(self, channel:int, amplitude_Vpp:float):
		# NOTE: bare `VOLT` is amplitude in whatever unit VOLT:UNIT is set to. The category's
		# contract is Vpp, so the unit is pinned rather than assumed - otherwise an instrument
		# left in Vrms by a previous user silently reinterprets every amplitude.
		self.write(f"SOUR{channel}:VOLT:UNIT VPP")
		self.write(f"SOUR{channel}:VOLT {amplitude_Vpp}")
	
	@superreturn
	def get_amplitude(self, channel:int):
		''' NOTE: this getter WRITES before it reads. `VOLT?` answers in whatever unit
		`VOLT:UNIT` is currently set to, so reading without pinning the unit returns a number
		whose meaning depends on how the instrument was left - Vrms and Vpp differ by 2*sqrt(2)
		on a sine, which is a plausible-looking wrong answer rather than an obvious one. The
		cost is that a get_amplitude() call leaves the front panel displaying Vpp.
		'''
		
		self.write(f"SOUR{channel}:VOLT:UNIT VPP")
		
		return self._parse_number(self.query(f"SOUR{channel}:VOLT?"))
	
	@superreturn
	def set_offset(self, channel:int, offset_V:float):
		self.write(f"SOUR{channel}:VOLT:OFFS {offset_V}")
	
	@superreturn
	def get_offset(self, channel:int):
		return self._parse_number(self.query(f"SOUR{channel}:VOLT:OFFS?"))
	
	@superreturn
	def set_phase(self, channel:int, phase_deg:float):
		# NOTE: the reference script sent `UNIT:ANGL:DEG`, which is a malformed version of the
		# real command - the value is a parameter, not another node. Degrees are pinned here for
		# the same reason the amplitude unit is.
		self.write("UNIT:ANGL DEG")
		self.write(f"SOUR{channel}:PHAS {phase_deg}")
	
	@superreturn
	def get_phase(self, channel:int):
		''' NOTE: writes before reading, for the same reason as get_amplitude() - `PHAS?` answers
		in whatever `UNIT:ANGL` is set to, and degrees vs radians is a factor of 57 hiding behind
		a number that looks fine. Leaves the instrument set to degrees.
		'''
		
		self.write("UNIT:ANGL DEG")
		
		return self._parse_number(self.query(f"SOUR{channel}:PHAS?"))
	
	def _duty_subsystem(self, channel:int) -> str:
		''' Which duty-cycle command applies to what this channel is currently generating.
		
		This instrument stores a duty cycle per function rather than one per channel, so the
		command is only well-defined once the waveform is known. The tracked waveform is used
		rather than a fresh query, to keep a duty-cycle call from costing two round trips; a
		caller that has not read the waveform since changing it can call get_waveform() first.
		'''
		
		wave = self.state.get(["channels", "waveform_type"], indices=[channel])
		
		return DUTY_SUBSYSTEM.get(wave)
	
	@superreturn
	def set_duty_cycle(self, channel:int, duty_pct:float):
		
		subsystem = self._duty_subsystem(channel)
		if subsystem is None:
			self.error(f"Channel {channel} is not generating a waveform that has a duty cycle.")
			return
		
		self.write(f"SOUR{channel}:FUNC:{subsystem}:DCYC {duty_pct}")
	
	@superreturn
	def get_duty_cycle(self, channel:int):
		
		subsystem = self._duty_subsystem(channel)
		if subsystem is None:
			return None
		
		return self._parse_number(self.query(f"SOUR{channel}:FUNC:{subsystem}:DCYC?"))
	
	@superreturn
	def set_output_enable(self, channel:int, enable:bool):
		self.write(f"OUTP{channel} {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_output_enable(self, channel:int):
		
		# Answers 0 or 1 rather than OFF/ON, so this goes through the numeric parser and not
		# str_to_bool().
		value = self._parse_number(self.query(f"OUTP{channel}?"))
		
		return None if value is None else bool(value)
	
	@superreturn
	def set_output_load(self, channel:int, load_ohm):
		
		code = "INF" if load_ohm == ArbitraryWaveformGenerator.LOAD_HIGH_Z else load_ohm
		
		self.write(f"OUTP{channel}:LOAD {code}")
	
	@superreturn
	def get_output_load(self, channel:int):
		
		value = self._parse_number(self.query(f"OUTP{channel}:LOAD?"))
		if value is None:
			return None
		
		# High impedance comes back as 9.9E37 rather than as a word. Compared against a threshold
		# rather than for equality: it is a sentinel the instrument prints, not a value it
		# measured, but an exact float comparison on a reply is a fragile way to detect it.
		if value >= HIGH_Z_THRESHOLD:
			return ArbitraryWaveformGenerator.LOAD_HIGH_Z
		
		return value
	
	@superreturn
	def set_output_polarity(self, channel:int, polarity:str):
		
		code = POLARITY_CODES.get(polarity)
		if code is None:
			self.error(f"Failed to recognize polarity >{polarity}<.")
			return
		
		self.write(f"OUTP{channel}:POL {code}")
	
	@superreturn
	def get_output_polarity(self, channel:int):
		
		response_str = self.query(f"OUTP{channel}:POL?")
		if not response_str:
			return None
		
		return POLARITY_CODES_INV.get(response_str.strip().upper())
