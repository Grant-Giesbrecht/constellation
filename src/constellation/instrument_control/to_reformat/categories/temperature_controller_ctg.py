from heimdallr.base import *

class PIDTemperatureControllerCtg(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn:str=""):
		super().__init__(address, log, expected_idn=expected_idn)
	
	@abstractmethod
	def set_setpoint(self, temp_K:float, channel:int=1):
		pass
	@abstractmethod
	def get_setpoint(self, channel:int=1):
		pass
	
	@abstractmethod
	def get_temp(self, channel:int=1):
		pass
	
	@abstractmethod
	def set_pid(self, P:float, I:float, D:float, channel:int=1):
		pass
	
	@abstractmethod
	def get_pid(self, channel:int=1):
		pass
	
	@abstractmethod
	def set_enable(self, enable:bool, channel:int=1):
		pass