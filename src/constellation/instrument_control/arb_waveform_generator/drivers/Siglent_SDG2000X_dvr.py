'''

https://siglentna.com/wp-content/uploads/dlm_uploads/2024/06/SDG_Programming-Guide_PG02-E05C.pdf
'''

from constellation.base import *
from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_ctg import *

class SiglentSDG2000X(BasicArbitraryWaveformGeneratorCtg):
	
	
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, relay=DirectSCPIRelay(), expected_idn="Siglent Technologies, SDG2", max_channels=2)
	
	# @superreturn
	def set_waveform(self, channel:int, wave:str):
		
		if wave == BasicArbitraryWaveformGeneratorCtg.WAVE_DC:
			code = "DC"
		elif wave == BasicArbitraryWaveformGeneratorCtg.WAVE_SINE:
			code = "SINE"
		elif wave == BasicArbitraryWaveformGeneratorCtg.WAVE_SQUARE:
			code = "SQUARE"
		elif wave == BasicArbitraryWaveformGeneratorCtg.WAVE_RAMP:
			code = "RAMP"
		elif wave == BasicArbitraryWaveformGeneratorCtg.WAVE_PULSE:
			code = "PULSE"
		elif wave == BasicArbitraryWaveformGeneratorCtg.WAVE_NOISE:
			code = "NOISE"
		elif wave == BasicArbitraryWaveformGeneratorCtg.WAVE_ARB:
			code = "ARB"
		else:
			self.error(f"Failed to recognize code >{wave}<.")
			return
		
		# Send command to instrument
		self.write(f"C{channel}:BSWV WVTP,{code}")
	
	def set_offset(self, channel:int, offset_V:float):
		
		#TODO: Error check offset level
		
		# Send command to instrument
		self.write(f"C{channel}:BSWV OFST,{offset_V}")
	
	def set_enable(self, channel:int, enable:bool):
		
		self.write(f"C{channel}:OUTP {bool_to_ONOFF(enable)}")