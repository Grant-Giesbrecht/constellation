from heimdallr.base import *

class RFSignalGeneratorCtg(Driver):
	
	POWER = "power[dBm]"
	FREQ = "freq[Hz]"
	ENABLE = "enable[bool]"
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn:str="", **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
		
		self.state[RFSignalGeneratorCtg.POWER] = None
		self.state[RFSignalGeneratorCtg.FREQ] = None
		self.state[RFSignalGeneratorCtg.ENABLE] = None
	
	@abstractmethod
	def set_power(self, p_dBm:float):
		pass
	@abstractmethod
	def get_power(self):
		pass
	
	@abstractmethod
	def set_freq(self, f_Hz:float):
		pass
	@abstractmethod
	def get_freq(self):
		pass
	
	@abstractmethod
	def set_enable_rf(self, enable:bool):
		pass
	@abstractmethod
	def get_enable_rf(self):
		pass
	
	def refresh_state(self):
		self.get_power()
		self.get_freq()
		self.get_enable_rf()
	
	def apply_state(self, new_state:dict):
		self.set_power(new_state[RFSignalGeneratorCtg.POWER])
		self.set_freq(new_state[RFSignalGeneratorCtg.FREQ])
		self.set_enable_rf(new_state[RFSignalGeneratorCtg.ENABLE])