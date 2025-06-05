from heimdallr.base import *

class VectorNetworkAnalyzerCtg0(Driver):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log)

class VectorNetworkAnalyzerCtg1(VectorNetworkAnalyzerCtg0):
	
	MEAS_S11 = "meas-s11"
	MEAS_S21 = "meas-s21"
	MEAS_S12 = "meas-s12"
	MEAS_S22 = "meas-s22"
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log)
	
	def set_freq_start(self, f_Hz:float, channel:int=1):
		pass
	
	def set_freq_end(self, f_Hz:float, channel:int=1):
		pass
	
	def set_power(self, p_dBm:float, channel:int=1):
		pass
	
	def set_num_points(self, points:int, channel:int=1):
		pass
	
	def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
		pass
	
	def clear_traces(self):
		pass
	
	def add_trace(self, channel:int, measurement:str):
		''' Returns trace number '''
		pass
	
	def get_trace(self, trace:int):
		
		pass

class VectorNetworkAnalyzerCtg2(VectorNetworkAnalyzerCtg1):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log)
	
	