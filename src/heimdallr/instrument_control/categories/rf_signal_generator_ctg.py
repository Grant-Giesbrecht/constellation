from heimdallr.base import *

class RFSignalGeneratorCtg0(Driver):
	
	def __init__(self, address:str, log:LogPile, expected_idn:str="", **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)

class RFSignalGeneratorCtg1(Driver):
	
	POWER = "power[dBm]"
	FREQ = "freq[Hz]"
	ENABLE = "enable[bool]"
	
	def __init__(self, address:str, log:LogPile, expected_idn:str="", **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
		
		self.state[RFSignalGeneratorCtg1.POWER] = None
		self.state[RFSignalGeneratorCtg1.FREQ] = None
		self.state[RFSignalGeneratorCtg1.ENABLE] = None
	
	def set_power(self, p_dBm:float):
		pass
	
	def set_freq(self, f_Hz:float):
		pass
	
	def set_enable_rf(self,enable:bool):
		pass