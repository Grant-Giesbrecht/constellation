'''

https://siglentna.com/wp-content/uploads/dlm_uploads/2024/06/SDG_Programming-Guide_PG02-E05C.pdf
'''

import re

from constellation.base import *
from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_ctg import *

# Category constant <-> the WVTP token the instrument uses. Kept as one table with a reverse
# lookup built from it, rather than an if-chain in the setter and a separate one in the getter:
# two hand-written mappings are how a getter ends up returning "SINE" where the category's
# vocabulary says "wave-sine", which round-trips against nothing and makes apply_state() a no-op
# after refresh_state().
WAVE_CODES = {
	ArbitraryWaveformGenerator.WAVE_SINE: "SINE",
	ArbitraryWaveformGenerator.WAVE_SQUARE: "SQUARE",
	ArbitraryWaveformGenerator.WAVE_RAMP: "RAMP",
	ArbitraryWaveformGenerator.WAVE_PULSE: "PULSE",
	ArbitraryWaveformGenerator.WAVE_NOISE: "NOISE",
	ArbitraryWaveformGenerator.WAVE_ARB: "ARB",
	ArbitraryWaveformGenerator.WAVE_DC: "DC",
}
WAVE_CODES_INV = {code: wave for wave, code in WAVE_CODES.items()}

# Unit suffixes the instrument appends to numeric values in a BSWV reply, and the SI prefixes that
# may precede them. Case matters and is the whole reason the prefix is matched separately: "MHZ"
# is mega and "mV" is milli, and stripping a fixed number of trailing characters (as this driver
# used to) silently turns "1.5MHZ" into 1.5.
#
# KNOWN LAXNESS, accepted deliberately: these two tables are applied as a cross product, so
# meaningless combinations like "M%" and "kS" parse as though they were real. Nothing an
# instrument actually emits is misparsed as a result - no base unit here begins with a character
# that is also a prefix, so no legitimate suffix is falsely split - but this is looser than
# parse_scpi_number()'s docstring implies. It also does not belong in an instrument driver at all:
# there are three copies of SI-prefix logic across this repo and stardust. The real fix is a unit
# registry in stardust.units, designed but deferred - see stardust/docs/units_design.md and
# todo_list.md P11.
_BASE_UNITS = ("HZ", "V", "VRMS", "VPP", "S", "DEG", "%")
_SI_PREFIXES = {"n": 1e-9, "u": 1e-6, "m": 1e-3, "k": 1e3, "K": 1e3, "M": 1e6, "G": 1e9}

_NUMBER_RE = re.compile(r"^\s*([+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*([A-Za-z%]*)\s*$")

def parse_scpi_number(text:str):
	''' Turns a value out of a BSWV reply ("1000HZ", "2.775V", "-0.5V", "1.5MHZ") into a float in
	base units. Returns None if it doesn't look like a number, rather than raising - a reply this
	driver can't read is a fact for the caller to see as a missing value, not a crash.
	'''
	
	if text is None:
		return None
	
	match = _NUMBER_RE.match(str(text))
	if match is None:
		return None
	
	value = float(match.group(1))
	suffix = match.group(2)
	
	if not suffix or suffix.upper() in _BASE_UNITS:
		return value
	
	if suffix[0] in _SI_PREFIXES and suffix[1:].upper() in _BASE_UNITS:
		return value * _SI_PREFIXES[suffix[0]]
	
	# An unrecognized suffix means the reply isn't shaped the way this parser assumes. Returning
	# the bare number would be a guess at the scale, so it doesn't.
	return None

class SiglentSDG2000X(ArbitraryWaveformGenerator):
	
	
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, **kwargs):
		super().__init__(address, log, relay=relay, expected_idn="Siglent Technologies, SDG2", max_channels=2, **kwargs)
	
	@superreturn
	def set_waveform(self, channel:int, wave:str):
		
		code = WAVE_CODES.get(wave)
		if code is None:
			self.error(f"Failed to recognize code >{wave}<.")
			return
		
		# Send command to instrument
		self.write(f"C{channel}:BSWV WVTP,{code}")
	
	def _read_wave_parameters(self, channel:int) -> dict:
		''' Queries a channel's basic-wave block and returns it as category-vocabulary values.
		
		Returns a plain dict and touches no state: the state tracker is only ever written through
		Driver.modify_state(), which the category's get_* methods do for us via @superreturn. An
		earlier version assigned self.state.channels[channel].frequency directly, which bypassed
		that choke point entirely and logged nothing.
		
		Keys present depend on the waveform: NOISE reports STDEV/MEAN and no FRQ or AMP at all, so
		a missing key is normal and comes back as None rather than a stale previous reading.
		'''
		
		response_str = self.query(f"C{channel}:BSWV?")
		# Response string follows the format:
		# 'C1:BSWV WVTP,SINE,FRQ,1000HZ,PERI,0.001S,AMP,2.775V,AMPVRMS,0.980963Vrms,OFST,0V,HLEV,1.3875V,LLEV,-1.3875V,PHSE,0\n'
		
		fields = self._parse_bswv(response_str)
		
		return {
			"waveform_type": WAVE_CODES_INV.get((fields.get("WVTP") or "").upper()),
			"frequency": parse_scpi_number(fields.get("FRQ")),
			"amplitude": parse_scpi_number(fields.get("AMP")),
			"offset": parse_scpi_number(fields.get("OFST")),
		}
	
	@staticmethod
	def _parse_bswv(response_str:str) -> dict:
		''' Splits a BSWV reply into its {keyword: value} pairs.
		
		Parsed as pairs rather than by fixed token index because the reply's shape depends on the
		waveform in force: a PULSE reply inserts DUTY/WIDTH/RISE/FALL and a NOISE reply drops FRQ
		and AMP entirely, so index 3 is the frequency only for the sine-like cases. Indexing was
		this driver's original approach and read the wrong field on every other waveform.
		'''
		
		if not response_str:
			return {}
		
		# Strip the 'C1:BSWV ' header, leaving a bare comma-separated key,value,key,value list.
		_, _, body = response_str.strip().partition(" ")
		
		tokens = [tok.strip() for tok in body.split(",")]
		
		return {tokens[i].upper(): tokens[i + 1] for i in range(0, len(tokens) - 1, 2) if tokens[i]}
	
	@superreturn
	def get_waveform(self, channel):
		return self._read_wave_parameters(channel)["waveform_type"]
	
	@superreturn
	def set_frequency(self, channel:int, freq_hz:float):
		self.write(f"C{channel}:BSWV FRQ,{freq_hz}")
	
	@superreturn
	def get_frequency(self, channel:int):
		return self._read_wave_parameters(channel)["frequency"]
	
	@superreturn
	def set_amplitude(self, channel:int, amplitude_Vpp:float):
		self.write(f"C{channel}:BSWV AMP,{amplitude_Vpp}")
	
	@superreturn
	def get_amplitude(self, channel:int):
		return self._read_wave_parameters(channel)["amplitude"]
	
	@superreturn
	def set_offset(self, channel:int, offset_V:float):
		
		# Send command to instrument
		self.write(f"C{channel}:BSWV OFST,{offset_V}")
	
	@superreturn
	def get_offset(self, channel:int):
		return self._read_wave_parameters(channel)["offset"]
	
	@superreturn
	def set_output_enable(self, channel:int, enable:bool):
		self.write(f"C{channel}:OUTP {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_output_enable(self, channel:int):
		response_str = self.query(f"C{channel}:OUTP?")
		# Example output: 'C1:OUTP OFF,LOAD,HZ,PLRT,NOR\n'
		
		if not response_str:
			return None
		
		# The state is the first value after the 'C1:OUTP ' header, before the LOAD/PLRT pairs.
		_, _, body = response_str.strip().partition(" ")
		
		return str_to_bool(body.split(",")[0].strip())
	
	def refresh_state(self):
		''' Overridden because one BSWV? query returns every basic-wave parameter at once - four
		category getters would be four round trips to read what the instrument reports in one.
		
		The values still go through modify_state(), the same choke point the category getters use;
		the only thing skipped is the redundant querying. Output enable is a separate command on
		this instrument, so it goes through its own getter.
		'''
		
		for ch_no in self.state.channels.get_range():
			
			params = self._read_wave_parameters(ch_no)
			
			for name in ("waveform_type", "frequency", "amplitude", "offset"):
				self.modify_state(None, ["channels", name], params[name], indices=[ch_no])
			
			self.get_output_enable(ch_no)
