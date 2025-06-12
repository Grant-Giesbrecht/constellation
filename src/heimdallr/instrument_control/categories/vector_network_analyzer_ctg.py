from heimdallr.base import *

class VectorNetworkAnalyzerCtg(Driver):
	
	MEAS_S11 = "meas-s11"
	MEAS_S21 = "meas-s21"
	MEAS_S12 = "meas-s12"
	MEAS_S22 = "meas-s22"
	
	FREQ_START = "freq-start[Hz]"
	FREQ_END = "freq-end[Hz]"
	POWER = "power[dBm]"
	NUM_POINTS = "num-points[]"
	RES_BW = "res-bw[Hz]"
	ENABLE = "rf-enable[bool]"
	
	def __init__(self, address:str, log:plf.LogPile, max_channels:int=24, max_traces:int=16, expected_idn:str="", **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
		
		self.max_channels = max_channels
		self.max_traces = max_traces # This is per-channel
		
		self.state[VectorNetworkAnalyzerCtg.FREQ_START] = []
		self.state[VectorNetworkAnalyzerCtg.FREQ_END] = []
		self.state[VectorNetworkAnalyzerCtg.POWER] = []
		self.state[VectorNetworkAnalyzerCtg.NUM_POINTS] = []
		self.state[VectorNetworkAnalyzerCtg.RES_BW] = []
		self.state[VectorNetworkAnalyzerCtg.ENABLE] = []
	
	@abstractmethod
	def set_freq_start(self, f_Hz:float, channel:int=1):
		pass
	@abstractmethod
	def get_freq_start(self, channel:int=1):
		pass
	
	@abstractmethod
	def set_freq_end(self, f_Hz:float, channel:int=1):
		pass
	@abstractmethod
	def get_freq_end(self, channel:int=1):
		pass
	
	@abstractmethod
	def set_power(self, p_dBm:float, channel:int=1):
		pass
	@abstractmethod
	def get_power(self, channel:int=1):
		pass
	
	@abstractmethod
	def set_num_points(self, points:int, channel:int=1):
		pass
	@abstractmethod
	def get_num_points(self, channel:int=1):
		pass
	
	@abstractmethod
	def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
		pass
	@abstractmethod
	def get_res_bandwidth(self, channel:int=1):
		pass
	
	@abstractmethod
	def clear_traces(self):
		pass
	
	@abstractmethod
	def add_trace(self, channel:int, measurement:str):
		''' Returns trace number '''
		pass
	
	# @abstractmethod
	# def get_trace(self, trace:int):
	# 	pass
	
	@abstractmethod
	def set_rf_enable(self, enable:bool):
		pass
	
	@abstractmethod
	def get_rf_enable(self):
		pass
	
	def refresh_state(self):
		self.warning(f">:qrefresh_state()< not implemented.")
		# self.get_freq_start() # Skipping - not sure how to handle querying number of traces
		# self.get_freq_end()
		# self.get_power()
		# self.get_num_points()
		# self.get_res_bandwidth()
		pass
	
	def apply_state(self, new_state):
		# Skipping - not sure how to handle querying number of traces
		self.warning(f">:qapply_state()< not implemented.")
	