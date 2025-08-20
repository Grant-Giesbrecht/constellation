from constellation.base import *
from constellation.helpers import lin_to_dB

def plot_vna_mag(data:dict, label:str=""):
	''' Helper function to plot the data output from a VNA get_trace_data() call.
	
	Args:
		data (dict): VNA trace data to plot
		label (str): Optional label for data
	
	Returns:
		None
	'''
	plt.plot(np.array(data['x'])/1e9, lin_to_dB(np.abs(data['y'])), label=label)
	
	plt.grid(True)
	plt.xlabel("Frequency [GHz]")
	plt.ylabel("S-Parameters [dB]")

class VNATraceState(InstrumentState):
	""" Class used to represent a trace that is active on the VNA.
	"""
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log)
		
		self.add_param("enabled", unit="bool", value=False)
		
		self.add_param("channel")
		self.add_param("id_str") # Trace name
		self.add_param("measurement") # For example: BasicVectorNetworkAnalyzerState.MEAS_S11
		self.add_param("format") # For example: BasicVectorNetworkAnalyzerState.FORM_LOG_MAG
		
		self.add_param("data", is_data=True, value={"freq_Hz":[], "meas_dB":[]})

class VNAChannelState(InstrumentState):
	""" Describes the state of one VNA channel.
	"""
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log)
		
		self.add_param("enabled", unit="bool", value=False)
		
		self.add_param("freq_start", unit="Hz")
		self.add_param("freq_end", unit="Hz")
		self.add_param("res_bw", unit="Hz")
		self.add_param("cal_enabled", unit="bool")
		
		self.add_param("num_points", unit="")
		self.add_param("power", unit="dBm")
	
	


class BasicVectorNetworkAnalyzerState(InstrumentState):

	def __init__(self, first_channel:int, num_channels:int, first_trace:int, num_traces:int, log:plf.LogPile=None):
		super().__init__(log)
		
		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		self.add_param("first_trace", unit="1", value=first_trace)
		self.add_param("num_traces", unit="1", value=num_traces)
		
		self.add_param("rf_enable", unit="bool")
		self.add_param("channels", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=VNAChannelState, log=log))
		self.add_param("traces", unit="", value=IndexedList(self.first_trace, self.num_traces, validate_type=VNATraceState, log=log))
		
		#NOTE: Unlike the oscilloscope state object, which immediately creates
		# all channel objects, this will dynamically create more as needed. This is to prevent the
		# state dict from getting massive (max possible size) when only a few traces or channels
		# are needed. THis is particularly important due to the large number of channels and traces
		# permitted for VNAs.
		
		# # Initialize channels
		# for ch in self.channels.get_range():
		# 	self.channels[ch] = VNAChannelState(log=log)
		
		# # Initialize traces
		# for tr in self.traces.get_range():
		# 	self.traces[tr] = VNATraceState(log=log)

class BasicVectorNetworkAnalyzerCtg(Driver):
	
	# Measurement options
	MEAS_S11 = "meas-s11"
	MEAS_S21 = "meas-s21"
	MEAS_S12 = "meas-s12"
	MEAS_S22 = "meas-s22"
	
	FORM_LOG_MAG = "form-log-mag"
	FORM_PHASE = "form-phase"
	FORM_SMITH = "form-smith"
	FORM_POLAR = "form-polar"
	FORM_SWR = "form-SWR"
	FORM_LIN_MAG = "form-lin-mag"
	FORM_REAL = "form-real"
	FORM_IMAG = "form-imag"
	FORM_SMITH_INV = "form-smith-inv" # Normalized admittance smith chart
	FORM_PHASE_UW = "form-phase-uw" # Unwrapped phase from start of sweep
	FORM_OTHER = "form-other"
	
	# Sweep types
	SWEEP_CONTINUOUS = "sweep-continuous"
	SWEEP_SINGLE = "sweep-single"
	SWEEP_OFF = "sweep-off"
	
	# State parameters
	CHANNELS = "channels"
	RF_ENABLE = "rf-enable[bool]"
	TRACES = "traces"
	
	def __init__(self, address:str, log:plf.LogPile, max_channels:int=24, max_traces:int=16, expected_idn:str="", first_channel:int=1, first_trace:int=1, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, first_channel_num=first_channel, first_trace_num=first_trace, **kwargs)
		
		self.max_channels = max_channels
		self.max_traces = max_traces # This is per-channel
		
		self.state = BasicVectorNetworkAnalyzerState(self.first_channel, self.max_channels, self.first_trace, self.max_traces, log=log)
	
	def find_trace(self, meas:str, format:str=FORM_LOG_MAG) -> str:
		''' Looks for a trace with the specified measurement and format, and if found,
		returns the trace's name. Returns NOne if no such trace is found.
		'''
		
		for tr in self.state.traces:
			if (tr.measurement == meas) and (tr.format == format):
				return tr.id_str
		
		return None
	
	@abstractmethod
	def _get_trace_idx(self, trace_name:str) -> int:
		pass
	
	@abstractmethod
	def valid_trace_name(self, name:str):
		pass
	
	@abstractmethod
	def set_freq_start(self, f_Hz:float, channel:int=1):
		self.modify_state(self.get_freq_start, ["channels", "freq_start"], f_Hz, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_freq_start(self, channel:int=1):
		return self.modify_state(None, ["channels", "freq_start"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_freq_end(self, f_Hz:float, channel:int=1):
		self.modify_state(self.get_freq_start, ["channels", "freq_end"], f_Hz, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_freq_end(self, channel:int=1):
		return self.modify_state(None, ["channels", "freq_end"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_power(self, p_dBm:float, channel:int=1):
		self.modify_state(self.get_power, ["channels", "power"], p_dBm, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_power(self, channel:int=1):
		return self.modify_state(None, ["channels", "power"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_num_points(self, points:int, channel:int=1):
		self.modify_state(self.get_num_points, ["channels", "num_points"], points, indices=[channel])
	
	@abstractmethod
	@enabledummy 
	def get_num_points(self, channel:int=1):
		return self.modify_state(None, ["channels", "num_points"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
		self.modify_state(self.get_res_bandwidth, ["channels", "res_bw"], rbw_Hz, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_res_bandwidth(self, channel:int=1):
		return self.modify_state(None, ["channels", "res_bw"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_cal_enabled(self, enable:bool, channel:int=1):
		self.modify_state(self.get_cal_enabled, ["channels", "cal_enabled"], enable, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_cal_enabled(self, channel:int=1):
		return self.modify_state(None, ["channels", "cal_enabled"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def clear_traces(self):
		self.state.traces.clear()
	
	@abstractmethod
	def add_trace(self, channel:int, trace_name:str, measurement:str) -> bool:
		''' Returns trace number '''
		pass
		#TODO: Add to state tracking somehow
	
	@abstractmethod
	@enabledummy
	def get_trace_data(self, trace_name:str):
		return self.modify_state(None, ["traces", "data"], self._super_hint, indices=[self._get_trace_idx(trace_name)])
	
	@abstractmethod
	def set_rf_enable(self, enable:bool):
		self.modify_state(self.get_rf_enable, ["rf_enable"], enable)
	
	@abstractmethod
	@enabledummy
	def get_rf_enable(self):
		return self.modify_state(None, ["rf_enable"], self._super_hint)
	
	@abstractmethod
	def set_rf_power(self, power_dBm:float, channel:int=1):
		self.modify_state(self.get_rf_power, ["channels", "power"], power_dBm, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_rf_power(self, channel:int=1):
		return self.modify_state(None, ["channels", "power"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def refresh_channels_and_traces(self):
		pass
	
	def refresh_state(self):
		self.refresh_channels_and_traces()
		self.get_rf_enable()
	
	def apply_state(self, new_state):
		# Skipping - not sure how to handle querying number of traces
		self.warning(f">:qapply_state()< not implemented.")
	