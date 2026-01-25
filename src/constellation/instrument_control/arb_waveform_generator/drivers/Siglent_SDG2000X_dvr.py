'''

https://siglentna.com/wp-content/uploads/dlm_uploads/2024/06/SDG_Programming-Guide_PG02-E05C.pdf
'''

from constellation.base import *
from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_ctg import *

class SiglentSDG2000X(ArbitraryWaveformGenerator):
	
	
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, relay=DirectSCPIRelay(), expected_idn="Siglent Technologies, SDG2", max_channels=2)
	
	@superreturn
	def set_waveform(self, channel:int, wave:str):
		
		if wave == ArbitraryWaveformGenerator.WAVE_DC:
			code = "DC"
		elif wave == ArbitraryWaveformGenerator.WAVE_SINE:
			code = "SINE"
		elif wave == ArbitraryWaveformGenerator.WAVE_SQUARE:
			code = "SQUARE"
		elif wave == ArbitraryWaveformGenerator.WAVE_RAMP:
			code = "RAMP"
		elif wave == ArbitraryWaveformGenerator.WAVE_PULSE:
			code = "PULSE"
		elif wave == ArbitraryWaveformGenerator.WAVE_NOISE:
			code = "NOISE"
		elif wave == ArbitraryWaveformGenerator.WAVE_ARB:
			code = "ARB"
		else:
			self.error(f"Failed to recognize code >{wave}<.")
			return
		
		# Send command to instrument
		self.write(f"C{channel}:BSWV WVTP,{code}")
	
	def _refresh_wave_parameters(self, channel:int):
		response_str = self.query(f"C{channel}:BSWV?")
		# Response string will follow format:
		# 'C1:BSWV WVTP,SINE,FRQ,1000HZ,PERI,0.001S,AMP,2.775V,AMPVRMS,0.980963Vrms,OFST,0V,HLEV,1.3875V,LLEV,-1.3875V,PHSE,0\n'
		
		# Split at all commas
		tokens = response_str.split(',')
		# Example result:
		# ['C1:BSWV WVTP', <- 0
		#  'SINE',
		#  'FRQ',
		#  '1000HZ', <- 3
		#  'PERI',
		#  '0.001S',
		#  'AMP',
		#  '2.775V', <- 7
		#  'AMPVRMS',
		#  '0.980963Vrms', <- 9
		#  'OFST',
		#  '0V', <- 11
		#  'HLEV',
		#  '1.3875V',
		#  'LLEV',
		#  '-1.3875V',
		#  'PHSE',
		#  '0\n']
		
		#
		try:
			wave_type = tokens[1]
			self.state.channels[channel].waveform_type = wave_type
			
			freq_str = tokens[3]
			self.state.channels[channel].frequency = float(freq_str[:-2])
			
			ampl_str = tokens[7]
			self.state.channels[channel].amplitude = float(ampl_str[:-1])
			
			offs_str = tokens[11]
			self.state.channels[channel].offset = float(offs_str[:-1])
		except Exception as e:
			pass
	
	@superreturn
	def get_waveform(self, channel):
		self._refresh_wave_parameters(channel)
		self._super_hint = self.state.channels[channel].waveform_type
	
	@superreturn
	def set_frequency(self, channel:int, freq_hz:float):
		self.write(f"C{channel}:BSWV FRQ,{freq_hz}")
	
	@superreturn
	def get_frequency(self, channel:int):
		self._refresh_wave_parameters(channel)
		self._super_hint = self.state.channels[channel].frequency
	
	@superreturn
	def set_amplitude(self, channel:int, amplitude_Vpp:float):
		self.write(f"C{channel}:BSWV AMP,{amplitude_Vpp}")
	
	@superreturn
	def get_amplitude(self, channel:int):
		self._refresh_wave_parameters(channel)
		self._super_hint = self.state.channels[channel].amplitude
	
	@superreturn
	def set_offset(self, channel:int, offset_V:float):
		
		# Send command to instrument
		self.write(f"C{channel}:BSWV OFST,{offset_V}")
	
	@superreturn
	def get_offset(self, channel:int):
		self._refresh_wave_parameters(channel)
		self._super_hint = self.state.channels[channel].offset
	
	@superreturn
	def set_output_enable(self, channel:int, enable:bool):
		self.write(f"C{channel}:OUTP {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_output_enable(self, channel:int):
		response_str = self.query(f"C{channel}:OUTP?")
		# Example output: 'C1:OUTP OFF,LOAD,HZ,PLRT,NOR\n'
		
		tokens = response_str.split(',')
		bool_words = tokens[0].split(' ')
		self._super_hint = str_to_bool(bool_words[1])
	
	def refresh_state(self):
		''' Because this model queries all parameters at once, refresh_state 
		is overridden.
		'''
		
		for ch_no in self.channels.get_range():
			self._refresh_wave_parameters(ch_no)