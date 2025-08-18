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

class VNATraceConfig(Packable):
	""" Class used to represent a trace that is active on the VNA.
	"""
	
	def __init__(self):
		self.num = 1 # Trace number
		self.channel = 1 # Channel that trace is defined on
		self.id_str = "Tr1"
		self.measurement = VectorNetworkAnalyzerCtg.MEAS_S11
		self.format = VectorNetworkAnalyzerCtg.FORM_LOG_MAG
		
		self.data = {}

class VNAChannelConfig(Packable):
	""" Describes the state of one VNA channel.
	"""
	
	FREQ_START = "freq-start[Hz]"
	FREQ_END = "freq-end[Hz]"
	POWER = "power[dBm]"
	NUM_POINTS = "num-points[1]"
	RES_BW = "res-bw[Hz]"
	
	def __init__(self):
		self.state[VNAChannelConfig.FREQ_START] = None
		self.state[VNAChannelConfig.FREQ_END] = None
		self.state[VNAChannelConfig.POWER] = None
		self.state[VNAChannelConfig.NUM_POINTS] = None
		self.state[VNAChannelConfig.RES_BW] = None
	
	def set_manifest(self):
		
		self.manifest.append("state")

class VectorNetworkAnalyzerState(InstrumentState):
	
	def __init__(self, log:plf.LogPile):
		super().__init__(log)
		
		self.channels = []
		self.rf_enable = []
		self.traces = []

class VectorNetworkAnalyzerCtg(Driver):
	
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
	
	def __init__(self, address:str, log:plf.LogPile, max_channels:int=24, max_traces:int=16, expected_idn:str="", **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
		
		self.max_channels = max_channels
		self.max_traces = max_traces # This is per-channel
		
		self.state = VectorNetworkAnalyzerState()
		
		self.state[VectorNetworkAnalyzerCtg.CHANNELS] = []
		self.state[VectorNetworkAnalyzerCtg.RF_ENABLE] = []
		self.state[VectorNetworkAnalyzerCtg.TRACES] = []
	
	
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
	