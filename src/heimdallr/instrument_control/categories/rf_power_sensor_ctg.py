from heimdallr.base import *

class RFPowerSensor(Driver):
	
	FREQ = "freq[Hz]"
	LAST_DATA = "last-data[dBm]"
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn=""):
		super().__init__(address, log, expected_idn=expected_idn)
	
	@abstractmethod
	def set_meas_frequency(self, f_Hz:float):
		pass
	@abstractmethod
	def get_meas_frequency(self) -> float:
		pass
	
	@abstractmethod
	def send_trigger(self, wait:bool=False):
		pass
	
	@abstractmethod
	def get_measurement(self):
		pass