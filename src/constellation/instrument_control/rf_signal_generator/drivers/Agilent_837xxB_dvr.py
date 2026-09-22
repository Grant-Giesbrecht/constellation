''' HP / Agilent 8371xB and 8373xB synthesized CW generators, single output.

Deliberately plain instruments: one RF connector, one carrier, no waveform of any kind. The four
models in the family - 83711B and 83712B (1 - 20 GHz and 10 MHz - 20 GHz) and the higher-power
83731B and 83732B - share this command set and differ only in frequency span and output power,
so one class covers all of them.

	HP 8371xB/8373xB Synthesized CW Generator User's Guide - SCPI command reference.

Two things about these instruments shape the driver:

- **They have no phase control.** There is no PHASe subsystem; the synthesizer's phase relative
  to the reference is simply not addressable. `set_phase`/`get_phase` are therefore marked
  `@feature_unavailable` rather than silently doing nothing - see docs/partial_compliance.md.
- **RF output on/off is `:POWer:STATe`, not `:OUTPut:STATe`.** This family predates the SCPI
  OUTPut subsystem's wide adoption, and the level and the on/off switch both live under POWer.

None of the SCPI below has been checked against hardware yet; every method is recorded
`unverified` in verification.yaml until a `pytest tests/hardware --address ...` run says
otherwise. Records are per model, so verifying an 83711B says nothing about an 83732B.
'''

from constellation.base import *
from constellation.instrument_control.rf_signal_generator.rf_signal_generator_ctg import *

# Category constant <-> the `:ROSCillator:SOURce` token, with a generated reverse lookup so the
# setter and the getter cannot drift into different vocabularies.
REF_CODES = {
	RFSignalGenerator.REF_INTERNAL: "INT",
	RFSignalGenerator.REF_EXTERNAL: "EXT",
}
REF_CODES_INV = {code: source for source, code in REF_CODES.items()}

class Agilent837xxB(RFSignalGenerator):
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="837", **kwargs):
		''' Args:
			expected_idn (str): Default matches every model in the family. Deliberately the model
				prefix and not the vendor word: these were sold across the HP-to-Agilent rename,
				so the same 83712B answers "HEWLETT-PACKARD,83712B,..." or
				"Agilent Technologies,83712B,..." depending on when it was built, and only the
				"837" is common to all of them. Pass your exact model for a stricter check.
		
		`max_channels` is not a parameter: every model has exactly one RF output, and letting a
		caller claim otherwise would build channel state that no command can ever reach.
		'''
		
		super().__init__(address, log, relay=relay, expected_idn=expected_idn, max_channels=1, **kwargs)
	
	def _parse_number(self, response_str:str):
		''' Floats out of a reply. This instrument answers numerics in bare scientific notation
		with an explicit sign ("+1.000000000000E+010") and no unit suffix, which float() takes
		directly. '''
		
		if not response_str:
			return None
		
		try:
			return float(response_str.strip())
		except ValueError:
			self.error(f"Could not read a number from >{response_str.strip()}<.")
			return None
	
	@superreturn
	def set_reference_source(self, source:str):
		
		code = REF_CODES.get(source)
		if code is None:
			self.error(f"Failed to recognize reference source >{source}<.")
			return
		
		self.write(f":ROSC:SOUR {code}")
	
	@superreturn
	def get_reference_source(self):
		
		response_str = self.query(":ROSC:SOUR?")
		if not response_str:
			return None
		
		# The reply is abbreviated to four characters at most, but slice anyway rather than
		# matching the whole string: a firmware that spells out INTERNAL would otherwise read as
		# an unrecognized source.
		code = response_str.strip().upper()[:3]
		
		source = REF_CODES_INV.get(code)
		if source is None:
			self.error(f"Instrument reports reference source >{response_str.strip()}<, which has no category equivalent.")
		
		return source
	
	@superreturn
	def set_frequency(self, channel:int, freq_hz:float):
		# NOTE: the mode is pinned first. :FREQ:CW sets the CW frequency but does not make the
		# instrument *use* it - a source left in sweep or list mode by a previous user accepts
		# the value and keeps sweeping, which looks like a frequency that refuses to change.
		self.write(":FREQ:MODE CW")
		self.write(f":FREQ:CW {freq_hz}")
	
	@superreturn
	def get_frequency(self, channel:int):
		return self._parse_number(self.query(":FREQ:CW?"))
	
	@superreturn
	def set_power(self, channel:int, power_dBm:float):
		# The unit is stated explicitly. Bare :POW:LEV is interpreted in whatever unit the
		# instrument was last left in, and dBm vs a linear voltage unit is a wrong answer that
		# looks entirely plausible.
		self.write(f":POW:LEV {power_dBm} DBM")
	
	@superreturn
	def get_power(self, channel:int):
		return self._parse_number(self.query(":POW:LEV?"))
	
	@superreturn
	def set_power_offset(self, channel:int, offset_dB:float):
		self.write(f":POW:OFFS {offset_dB} DB")
	
	@superreturn
	def get_power_offset(self, channel:int):
		return self._parse_number(self.query(":POW:OFFS?"))
	
	@feature_unavailable("8371xB/8373xB have no phase offset control - the command set has no PHASe subsystem")
	def set_phase(self, channel:int, phase_deg:float):
		pass
	
	@feature_unavailable("8371xB/8373xB have no phase offset control - the command set has no PHASe subsystem")
	def get_phase(self, channel:int):
		pass
	
	@superreturn
	def set_alc_enable(self, channel:int, enable:bool):
		self.write(f":POW:ALC:STAT {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_alc_enable(self, channel:int):
		
		# Answers 0 or 1 rather than OFF/ON, so this goes through the numeric parser and not
		# str_to_bool().
		value = self._parse_number(self.query(":POW:ALC:STAT?"))
		
		return None if value is None else bool(value)
	
	@superreturn
	def set_output_enable(self, channel:int, enable:bool):
		self.write(f":POW:STAT {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_output_enable(self, channel:int):
		
		value = self._parse_number(self.query(":POW:STAT?"))
		
		return None if value is None else bool(value)
