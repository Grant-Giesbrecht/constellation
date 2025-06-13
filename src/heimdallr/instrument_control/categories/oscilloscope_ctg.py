from heimdallr.base import *
from heimdallr.networking.net_client import *

class OscilloscopeCtg0(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn="", **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
	
	def refresh_state(self):
		pass
	
class OscilloscopeCtg1(OscilloscopeCtg0):
	
	DIV_TIME = "div-time[s]"
	OFFSET_TIME = "offset-time[s]"
	DIV_VOLT = "div-volt[V]"
	OFFSET_VOLT = "offset-volt[V]"
	CHAN_EN = "chan_en[bool]"
	WAVEFORM = "waveform[V]"
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn="", max_channels:int=1, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
		
		self.state[OscilloscopeCtg1.DIV_TIME] = None
		self.state[OscilloscopeCtg1.OFFSET_TIME] = []
		self.state[OscilloscopeCtg1.DIV_VOLT] = []
		self.state[OscilloscopeCtg1.OFFSET_VOLT] = []
		self.state[OscilloscopeCtg1.CHAN_EN] = []
		self.state[OscilloscopeCtg1.WAVEFORM] = []
		
		self.max_channels = max_channels
		self.dummy_state_machine['div_time'] = 10e-3
	
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Function expected to behave as the "real" equivalents. ie. write commands don't
		need to return anything, reads commands or similar should. What is returned here
		should mimic what would be returned by the "real" function if it were connected to
		hardware.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			# Respond to dummy function
			match func_name:
				case "set_div_time":
					self.dummy_state_machine['div_time'] = args[0]
					return None
				case "get_div_time":
					return self.dummy_state_machine['div_time']
				case "set_offset_time":
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	
	@abstractmethod
	def set_div_time(self, time_s:float):
		pass
	@abstractmethod
	def get_div_time(self):
		pass
	
	@abstractmethod
	def set_offset_time(self, time_s:float):
		pass
	@abstractmethod
	def get_offset_time(self):
		pass
	
	@abstractmethod
	def set_div_volt(self, channel:int, volt_V:float):
		pass
	@abstractmethod
	def get_div_volt(self, channel:int):
		pass
	
	@abstractmethod
	def set_offset_volt(self, channel:int, volt_V:float):
		pass
	@abstractmethod
	def get_offset_volt(self, channel:int):
		pass
	
	@abstractmethod
	def set_chan_enable(self, channel:int, enable:bool):
		self.modify_state(self.get_div_time, "CHAN_ENABLE")
		pass
	@abstractmethod
	def get_chan_enable(self, channel:int):
		self.modify_state(self.get_div_time, "CHAN_ENABLE")
		pass
	
	@abstractmethod
	def get_waveform(self, channel:int):
		pass
	
	def refresh_state(self):
		self.get_div_time()
		self.get_offset_time()
		for ch in range(1, self.max_channels+1):
			self.get_div_volt(ch)
			self.get_offset_volt(ch)
			self.get_chan_enable(ch)
	
	def apply_state(self, new_state:dict):
		self.set_div_time(new_state[OscilloscopeCtg1.DIV_TIME])
		self.set_offset_time(new_state[OscilloscopeCtg1.OFFSET_TIME])
		for ch in range(1, self.max_channels+1):
			self.set_div_volt(ch, new_state[OscilloscopeCtg1.DIV_VOLT][ch-1])
			self.set_offset_volt(ch, new_state[OscilloscopeCtg1.OFFSET_VOLT][ch-1])
			self.set_chan_enable(ch, new_state[OscilloscopeCtg1.CHAN_EN][ch-1])
		

class RemoteOscilloscopeCtg1(RemoteInstrument, OscilloscopeCtg1):
	''' This class mirrors the function in OscilloscopeCtg1, but each function
	is decorated with RemoteFunction. This lets a T/C client create RemoteInstruments
	for this category of instrument using this class and callings its functions, rather
	than creating a RemoteInstrument object and always calling remote_call.'''
	
	def __init__(self, ca:ClientAgent, log:plf.LogPile, remote_id:str=None, remote_address:str=None):
		super().__init__(ca, log, remote_id=remote_id, remote_address=remote_address)
	
	@remotefunction
	def set_div_time(self, time_s:float):
		pass
	
	@remotefunction
	def get_div_time(self):
		pass
	
	@remotefunction
	def set_offset_time(self, time_s:float):
		pass
	
	@remotefunction
	def get_offset_time(self):
		pass
	
	@remotefunction
	def set_div_volt(self, channel:int, volt_V:float):
		pass
	
	@remotefunction
	def get_div_volt(self, channel:int):
		pass
	
	@remotefunction
	def set_offset_volt(self, channel:int, volt_V:float):
		pass
	
	@remotefunction
	def get_offset_volt(self, channel:int):
		pass
	
	@remotefunction
	def set_chan_enable(self, channel:int, enable:bool):
		pass
	
	@remotefunction
	def get_chan_enable(self, channel:int):
		pass
	
	@remotefunction
	def get_waveform(self, channel:int):
		pass
	
	@remotefunction
	def refresh_state(self):
		pass
	
	@remotefunction
	def apply_state(self, new_state:dict):
		pass
	
class OscilloscopeCtg2(OscilloscopeCtg1):
	
	# Measurement options
	MEAS_VMAX = 0
	MEAS_VMIN = 1
	MEAS_VAVG = 2
	MEAS_VPP  = 3
	MEAS_FREQ = 4
	
	# Statistics options for measurement options
	STAT_NONE = 0
	STAT_AVG = 1
	STAT_MAX = 2
	STAT_MIN = 3
	STAT_CURR = 4
	STAT_STD = 5
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn="", **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
	
	@abstractmethod
	def add_measurement(self):
		pass
	
	@abstractmethod
	def get_measurement(self):
		pass
	
	def refresh_state(self):
		super().refresh_state()
	
