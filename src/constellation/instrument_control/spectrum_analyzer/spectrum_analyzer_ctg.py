from constellation.base import *
from constellation.networking.net_client import *

class SpectrumAnalyzerTraceState(InstrumentState):
	
	__state_fields__ = ("div_volt", "offset_volt", "chan_en", "waveform")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		

class SpectrumAnalyzerChannelState(InstrumentState):
	
	__state_fields__ = ("div_volt", "offset_volt", "chan_en", "waveform")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("div_volt", unit="V")
		self.add_param("offset_volt", unit="V")
		
		self.add_param("traces", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=SpectrumAnalyzerTraceState, log=log))
		
		self.add_param("waveform", unit="", is_data=True, value={"time_S":[], "volt_V":[]})

class SpectrumAnalyzerState(InstrumentState):
	
	__state_fields__ = ("first_channel", "num_channels", "ndiv_horiz", "ndiv_vert", "div_time", "offset_time", "channels")
	
	def __init__(self, first_channel:int, num_channels:int, ndiv_horiz, ndiv_vert, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		
		self.add_param("ndiv_horiz", unit="1", value=ndiv_horiz)
		self.add_param("ndiv_vert", unit="1", value=ndiv_vert)
		
		self.add_param("div_time", unit="s")
		self.add_param("offset_time", unit="s")
		
		self.add_param("channels", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=SpectrumAnalyzerChannelState, log=log))
		
		for ch_no in self.channels.get_range():
			self.channels[ch_no] = SpectrumAnalyzerChannelState(log=log)

class SpectrumAnalyzerCtg(Driver):
	
	SWEEP_CONTINUOUS = "sweep-continuous"
	SWEEP_SINGLE = "sweep-single"
	SWEEP_OFF = "sweep-off"
	
	FREQ_START = "freq-start[Hz]"
	FREQ_END = "freq-end[Hz]"
	NUM_POINTS = "num-points[]"
	RES_BW = "res-bw[Hz]"
	CONTINUOUS_TRIG_EN = "continuous-trig[bool]"
	REF_LEVEL = "ref-level[dBm]"
	Y_DIV = "y-div[dB]"
	
	TRACE_DATA = "traces"
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn:str="", dummy:bool=False, relay:CommandRelay=None, max_channels:int=1, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, relay=relay, **kwargs)
		
		self.max_channels = max_channels
		
		self.state[SpectrumAnalyzerCtg.FREQ_START] = None
		self.state[SpectrumAnalyzerCtg.FREQ_END] = None
		self.state[SpectrumAnalyzerCtg.NUM_POINTS] = []
		self.state[SpectrumAnalyzerCtg.RES_BW] = None
		self.state[SpectrumAnalyzerCtg.TRACE_DATA] = []
		self.state[SpectrumAnalyzerCtg.CONTINUOUS_TRIG_EN] = None
		self.state[SpectrumAnalyzerCtg.REF_LEVEL] = None
		self.state[SpectrumAnalyzerCtg.Y_DIV] = None
		
		self.data[SpectrumAnalyzerCtg.TRACE_DATA] = IndexedList(self.first_channel, self.max_channels, log=self.log)
		
		if self.dummy:
			self.init_dummy_state()
	
	def init_dummy_state(self) -> None:
		pass
	
	def remake_dummy_waves(self) -> None:
		pass
	
	def dummy_responder(self, func_name, *args, **kwargs):
		pass
	
	@abstractmethod
	def set_freq_start(self, f_Hz:float):
		self.modify_state(self.get_freq_start, SpectrumAnalyzerCtg.FREQ_START, f_Hz)
	
	@abstractmethod
	@enabledummy
	def get_freq_start(self):
		return self.modify_state(None, SpectrumAnalyzerCtg.FREQ_START, self._super_hint)
	
	@abstractmethod
	def set_freq_end(self, f_Hz:float):
		self.modify_state(self.get_freq_end, SpectrumAnalyzerCtg.FREQ_END, f_Hz)
	
	@abstractmethod
	@enabledummy
	def get_freq_end(self):
		return self.modify_state(None, SpectrumAnalyzerCtg.FREQ_END, self._super_hint)
	
	@abstractmethod
	def set_num_points(self, points:int, channel:int=1):
		self.modify_state(self.get_num_points, SpectrumAnalyzerCtg.NUM_POINTS, points, channel=channel)
	
	@abstractmethod
	@enabledummy
	def get_num_points(self, channel:int=1):
		return self.modify_state(None, SpectrumAnalyzerCtg.NUM_POINTS, self._super_hint)
	
	@abstractmethod
	def set_res_bandwidth(self, rbw_Hz:float):
		self.modify_state(self.get_res_bandwidth, SpectrumAnalyzerCtg.RES_BW, rbw_Hz)
	
	@abstractmethod
	@enabledummy
	def get_res_bandwidth(self):
		return self.modify_state(None, SpectrumAnalyzerCtg.RES_BW, self._super_hint)
	
	@abstractmethod
	def clear_traces(self):
		#TODO: Reset trace state tracking model
		pass
	
	@abstractmethod
	def add_trace(self, channel:int, measurement:str):
		''' Returns trace number '''
		#TODO: Update trace state tracking model
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
	
	def refresh_state(self):
		self.get_freq_start()
		self.get_freq_end()
		# self.get_num_points() # Skipping because not sure how best to handle traces yet
		self.get_res_bandwidth()
		self.get_continuous_trigger()
		self.get_ref_level()
		self.get_y_div()
	
	def apply_state(self):
		self.set_freq_start(SpectrumAnalyzerCtg.FREQ_START)
		self.set_freq_end(SpectrumAnalyzerCtg.FREQ_END)
		self.set_res_bandwidth(SpectrumAnalyzerCtg.RES_BW)
		self.set_continuous_trigger(SpectrumAnalyzerCtg.CONTINUOUS_TRIG_EN)
		self.set_ref_level(SpectrumAnalyzerCtg.REF_LEVEL)
		self.set_y_div(SpectrumAnalyzerCtg.Y_DIV)