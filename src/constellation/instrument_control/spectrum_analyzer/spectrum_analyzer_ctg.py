from constellation.base import *
import stardust.algorithm as stal

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
		''' Seeds a plausible starting state so dummy mode has something to read back.
		
		Since modify_state() became the single dummy dispatch point, a dummy getter returns
		whatever is tracked in state - which means a category that never seeds its state returns
		None from every getter, and the dummy instrument is useless for testing anything
		downstream. These are ordinary defaults, not synthetic data: they go through the normal
		setters, exactly as the oscilloscope and power supply do.
		'''
		
		self.set_freq_start(1e9)
		self.set_freq_end(3e9)
		self.set_res_bandwidth(100e3)
		self.set_continuous_trigger(True)
		self.set_ref_level(0)
		self.set_y_div(10)
		
		self.remake_dummy_trace()
	
	def remake_dummy_trace(self) -> None:
		''' Regenerates a synthetic spectrum for every populated trace, consistent with the
		current dummy state.
		
		Unlike the settings above, this IS invented data - a spectrum analyzer's trace is a
		measurement, and there is nothing in state to read it back from. The shape matches what
		a real driver returns (x/y plus units, see Siglent_SSA3000X_dvr.get_trace_data), not
		SpectrumAnalyzerTraceState's stale default value of {"time_S":[], "volt_V":[]} - that
		default is itself wrong for this category and is logged under P11.
		
		Returns:
			None
		'''
		
		f_start = self.state.get(["freq_start"])
		f_end = self.state.get(["freq_end"])
		ref_level = self.state.get(["ref_level"])
		y_div = self.state.get(["y_div_scale"])
		
		# A partially-seeded state shouldn't crash the synthesis - fall back to the same defaults
		# init_dummy_state() would have used.
		if f_start is None:
			f_start = 1e9
		if f_end is None or f_end <= f_start:
			f_end = f_start + 2e9
		if ref_level is None:
			ref_level = 0.0
		if y_div is None:
			y_div = 10.0
		
		npoints = 751
		freqs = np.linspace(f_start, f_end, npoints)
		
		# Noise floor near the bottom of the displayed range, plus a couple of Lorentzian peaks -
		# enough structure that peak-finding or plotting code has something real to chew on.
		noise_floor = ref_level - y_div * self.state.get(["ndiv_vert"])
		amplitudes = np.full(npoints, float(noise_floor))
		amplitudes += np.array([stal.randrange(-1.5, 1.5) for _ in range(npoints)])
		
		span = f_end - f_start
		for centre_frac, height, width_frac in ((0.35, 0.85, 0.004), (0.62, 0.45, 0.010)):
			centre = f_start + span * centre_frac
			width = span * width_frac
			amplitudes += (ref_level - noise_floor) * height / (1 + ((freqs - centre)/width)**2)
		
		for t_idx, trace in self.state.traces.populated_items():
			trace.waveform = {
				"x": [float(f) for f in freqs],
				"y": [float(a) for a in amplitudes],
				"x_units": "Hz",
				"y_units": "dBm",
			}
	
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Supplies SYNTHETIC dummy values only - see Oscilloscope.dummy_responder. Plain
		set_*/get_* methods are handled generically by modify_state() and need no case here.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			match func_name:
				case "get_trace_data":
					# A trace is measurement data, not a setting - there is nothing tracked to
					# read back until one has been invented.
					self.remake_dummy_trace()
					trace = args[0] if len(args) > 0 else kwargs.get("trace", self.state.first_trace)
					rval = self.state.get(["traces", "waveform"], indices=[trace])
				case _:
					return super().dummy_responder(func_name, *args, **kwargs)
			
			self.debug(f"Dummy responder sending >{protect_str(rval)}< to synthetic function (>{func_name}<).")
			return rval
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	
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