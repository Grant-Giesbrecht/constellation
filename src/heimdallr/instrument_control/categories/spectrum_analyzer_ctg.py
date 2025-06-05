from heimdallr.base import *
from heimdallr.networking.net_client import *

class SpectrumAnalyzerCtg(Driver):
	
	SWEEP_CONTINUOUS = "sweep-continuous"
	SWEEP_SINGLE = "sweep-single"
	SWEEP_OFF = "sweep-off"
	
	FREQ_START = "freq-start[Hz]"
	FREQ_END = "freq-end[Hz]"
	NUM_POINTS = "num-points[]"
	RES_BW = "res-bw[Hz]"
	TRACE_DATA = "traces[dBm]"
	CONTINUOUS_TRIG_EN = "continuous-trig[bool]"
	REF_LEVEL = "ref-level[dBm]"
	Y_DIV = "y-div[dB]"
	
	def __init__(self, address:str, log:LogPile, expected_idn:str=""):
		super().__init__(address, log, expected_idn=expected_idn)
		
		self.state[SpectrumAnalyzerCtg.FREQ_START] = None
		self.state[SpectrumAnalyzerCtg.FREQ_END] = None
		self.state[SpectrumAnalyzerCtg.NUM_POINTS] = []
		self.state[SpectrumAnalyzerCtg.RES_BW] = None
		self.state[SpectrumAnalyzerCtg.TRACE_DATA] = []
		self.state[SpectrumAnalyzerCtg.CONTINUOUS_TRIG_EN] = None
		self.state[SpectrumAnalyzerCtg.REF_LEVEL] = None
		self.state[SpectrumAnalyzerCtg.Y_DIV] = None
		
	@abstractmethod
	def set_freq_start(self, f_Hz:float):
		pass
	@abstractmethod
	def get_freq_start(self):
		pass
	
	@abstractmethod
	def set_freq_end(self, f_Hz:float):
		pass
	@abstractmethod
	def get_freq_end(self):
		pass
	
	@abstractmethod
	def set_num_points(self, points:int, channel:int=1):
		pass
	@abstractmethod
	def get_num_points(self, channel:int=1):
		pass
	
	@abstractmethod
	def set_res_bandwidth(self, rbw_Hz:float):
		pass
	@abstractmethod
	def get_res_bandwidth(self):
		pass
	
	@abstractmethod
	def clear_traces(self):
		pass
	
	@abstractmethod
	def add_trace(self, channel:int, measurement:str):
		''' Returns trace number '''
		pass
	
	@abstractmethod
	def get_trace_data(self, channel:int):
		pass
	
	@abstractmethod
	def set_continuous_trigger(self, enable:bool):
		pass
	@abstractmethod
	def get_continuous_trigger(self):
		pass
	
	@abstractmethod
	def send_manual_trigger(self, send_cls:bool=True):
		pass
	
	@abstractmethod
	def set_ref_level(self, ref_dBm:float):
		pass
	@abstractmethod
	def get_ref_level(self):
		pass
	
	@abstractmethod
	def set_y_div(self, step_dB:float):
		pass
	@abstractmethod
	def get_y_div(self):
		pass
	
	
#TODO: Make one of these for all instruments
#TODO: Flesh out
class SpectrumAnalyzerRemote(RemoteInstrument):
	
	def __init__(self):
		super().__init__()
	
	# Without the decorator, it looks like this
	def set_freq_start(self, f_Hz:float, channel:int=1):
		self.remote_call('set_freq_start', f_Hz, channel)
	
	# With the decorator, it looks like this
	@remotefunction
	def set_freq_end(self, f_Hz:float, channel:int=1):
		pass