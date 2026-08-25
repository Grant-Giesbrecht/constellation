from constellation.base import *

class SpectrumAnalyzerTraceState(InstrumentState):
	
	__state_fields__ = ("waveform",) #Note: Require a comma at end so it is seen a tuple
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("waveform", unit="", is_data=True, value={"time_S":[], "volt_V":[]})
		
		
class SpectrumAnalyzerState(InstrumentState):
	
	__state_fields__ = ("first_trace", "num_traces", "ndiv_horiz", "ndiv_vert", "freq_start", "freq_end", "res_bw", "continuous_trig_en", "ref_level", "y_div_scale", "traces")
	
	def __init__(self, first_trace:int, num_traces:int, ndiv_horiz, ndiv_vert, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("ndiv_horiz", unit="1", value=ndiv_horiz)
		self.add_param("ndiv_vert", unit="1", value=ndiv_vert)
		self.add_param("first_trace", unit="1", value=first_trace)
		self.add_param("num_traces", unit="1", value=num_traces)
		
		self.add_param("freq_start", unit="Hz")
		self.add_param("freq_end", unit="Hz")
		# self.add_param("num_points", unit="1") # I think this doesnt actually exist
		self.add_param("res_bw", unit="Hz")
		self.add_param("continuous_trig_en", unit="bool")
		self.add_param("ref_level", unit="dBm")
		self.add_param("y_div_scale", unit="dB")
		
		self.add_param("traces", unit="", value=IndexedList(self.first_trace, self.num_traces, validate_type=SpectrumAnalyzerTraceState, log=log))
		
		# TODO: In future, allow to populate more traces
		self.traces[self.first_trace] = SpectrumAnalyzerTraceState(self.log)
		
		
class SpectrumAnalyzer(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn:str="", dummy:bool=False, relay:CommandRelay=None, num_traces:int=1, first_trace:int=1, ndiv_horiz:int=8, ndiv_vert:int=8, **kwargs):
		# State must be built BEFORE super().__init__(), which requires it positionally and
		# also hands it to discover_mixins(). Building it afterwards left Driver.__init__ with no
		# state at all, which made every SpectrumAnalyzer driver impossible to construct.
		_state = SpectrumAnalyzerState(first_trace=first_trace, num_traces=num_traces, ndiv_horiz=ndiv_horiz, ndiv_vert=ndiv_vert, log=log)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy, first_trace_num=first_trace, **kwargs)
		
		if self.dummy:
			self.init_dummy_state()
	
	def init_dummy_state(self) -> None:
		pass
	
	def remake_dummy_trace(self) -> None:
		pass
	
	@abstractmethod
	def set_freq_start(self, f_Hz:float):
		self.modify_state(self.get_freq_start, ["freq_start"], f_Hz)
	
	@abstractmethod
	def get_freq_start(self):
		return self.modify_state(None, ["freq_start"], self._super_hint)
	
	@abstractmethod
	def set_freq_end(self, f_Hz:float):
		self.modify_state(self.get_freq_end, ["freq_end"], f_Hz)
	
	@abstractmethod
	def get_freq_end(self):
		return self.modify_state(None, ["freq_end"], self._super_hint)
	
	# @abstractmethod
	# def set_num_points(self, points:int):
	# 	self.modify_state(self.get_num_points, ["num_points"], points)
	
	# @abstractmethod
	# @enabledummy
	# def get_num_points(self, channel:int=1):
	# 	return self.modify_state(None, ["num_points"], self._super_hint)
	
	@abstractmethod
	def set_res_bandwidth(self, rbw_Hz:float):
		self.modify_state(self.get_res_bandwidth, ["res_bw"], rbw_Hz)
	
	@abstractmethod
	def get_res_bandwidth(self):
		return self.modify_state(None, ["res_bw"], self._super_hint)
	
	#TODO: Add ability to add new traces
	# @abstractmethod
	# def clear_traces(self):
	# 	#TODO: Reset trace state tracking model
	# 	pass
	
	
	# @abstractmethod
	# def add_trace(self, channel:int, measurement:str):
	# 	''' Returns trace number '''
	# 	#TODO: Update trace state tracking model
	# 	pass
	
	@abstractmethod
	@enabledummy
	def get_trace_data(self, trace:int, **kwargs):
		''' Reads one trace back from the instrument and stores it in that trace's state.
		
		**kwargs absorbs driver-specific transfer options (e.g. use_ascii_transfer on the
		Siglent SSA3000X) so a driver can extend this without a category signature change -
		superreturn forwards whatever args/kwargs the driver-level call received.
		
		Trace data is measurement data rather than a setting, so this keeps @enabledummy: in
		dummy mode there is nothing in state to read back until a trace has been synthesized.
		'''
		return self.modify_state(None, ["traces", "waveform"], self._super_hint, indices=[trace])
	
	@abstractmethod
	def set_continuous_trigger(self, enable:bool):
		self.modify_state(self.get_continuous_trigger, ["continuous_trig_en"], enable)
	
	@abstractmethod
	def get_continuous_trigger(self):
		return self.modify_state(None, ["continuous_trig_en"], self._super_hint)
	
	@abstractmethod
	def send_manual_trigger(self, send_cls:bool=True):
		pass
	
	@abstractmethod
	def set_ref_level(self, ref_dBm:float):
		self.modify_state(self.get_ref_level, ["ref_level"], ref_dBm)
	
	@abstractmethod
	def get_ref_level(self):
		return self.modify_state(None, ["ref_level"], self._super_hint)
	
	@abstractmethod
	def set_y_div(self, step_dB:float):
		self.modify_state(self.get_y_div, ["y_div_scale"], step_dB)
	
	@abstractmethod
	def get_y_div(self):
		return self.modify_state(None, ["y_div_scale"], self._super_hint)
	
	def refresh_state(self):
		self.get_freq_start()
		self.get_freq_end()
		# NOTE: num_points is deliberately absent - set_num_points/get_num_points are
		# commented out below and the parameter is not in __state_fields__.
		self.get_res_bandwidth()
		self.get_continuous_trigger()
		self.get_ref_level()
		self.get_y_div()
		
		# iterate over all traces and get data
		for t_idx in self.state.traces.get_populated():
			self.get_trace_data(t_idx)
	
	def refresh_data(self):
		# iterate over all traces and get data
		for t_idx in self.state.traces.get_populated():
			self.get_trace_data(t_idx)
	
	def apply_state(self):
		self.set_freq_start(self.state.freq_start)
		self.set_freq_end(self.state.freq_end)
		# NOTE: num_points deliberately absent - see refresh_state().
		self.set_res_bandwidth(self.state.res_bw)
		self.set_continuous_trigger(self.state.continuous_trig_en)
		self.set_ref_level(self.state.ref_level)
		self.set_y_div(self.state.y_div_scale)