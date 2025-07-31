from heimdallr.base import *
from heimdallr.networking.net_client import *
	
class BasicOscilloscopeCtg(Driver):
	
	DIV_TIME = "div-time[s]"
	OFFSET_TIME = "offset-time[s]"
	DIV_VOLT = "div-volt[V]"
	OFFSET_VOLT = "offset-volt[V]"
	CHAN_EN = "chan_en[bool]"
	WAVEFORM = "waveform[V]"
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn="", max_channels:int=1, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, **kwargs)
		
		self.state[BasicOscilloscopeCtg.DIV_TIME] = None
		self.state[BasicOscilloscopeCtg.OFFSET_TIME] = []
		self.state[BasicOscilloscopeCtg.DIV_VOLT] = []
		self.state[BasicOscilloscopeCtg.OFFSET_VOLT] = []
		self.state[BasicOscilloscopeCtg.CHAN_EN] = []
		
		self.data[BasicOscilloscopeCtg.WAVEFORM] = []
		
		self.max_channels = max_channels
	
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Function expected to behave as the "real" equivalents. ie. write commands don't
		need to return anything, reads commands or similar should. What is returned here
		should mimic what would be returned by the "real" function if it were connected to
		hardware.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			# Check for known functions
			found = True
			adjective = ""
			match func_name:
				case "set_div_time":
					rval = None
				case "get_div_time":
					rval = self.state[BasicOscilloscopeCtg.DIV_TIME]
				case "set_offset_time":
					rval = None
				case "get_offset_time":
					rval = self.state[BasicOscilloscopeCtg.OFFSET_TIME]
				case "set_div_volt":
					rval = None
				case "get_div_volt":
					rval = self.state[BasicOscilloscopeCtg.DIV_VOLT]
				case "set_offset_volt":
					rval = None
				case "get_offset_volt":
					rval = self.state[BasicOscilloscopeCtg.OFFSET_VOLT]
				case "set_chan_enable":
					rval = None
				case "get_chan_enable":
					rval = self.state[BasicOscilloscopeCtg.CHAN_EN].get_ch_val([args[0]])
				case _:
					found = False
				# case "set_offset_time":
			
			# If function was found, label as recognized, else check match for general getter or setter
			if found:
				adjective = "recognized"
			else:
				if "set_" == func_name[:4]:
					rval = -1
					adjective = "set_"
				elif "get_" == func_name[:4]:
					rval = None
					adjective = "get_"
				else:
					rval = None
					adjective = "unrecognized"
				
			self.debug(f"Default dummy responder sending >{rval}< to {adjective} function (>{func_name}<).")
			return rval
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	
	@abstractmethod
	@enabledummy
	def set_div_time(self, time_s:float):
		self.modify_state(self.get_div_time, BasicOscilloscopeCtg.DIV_TIME, time_s)
	
	@abstractmethod
	@enabledummy
	def get_div_time(self):
		return self.modify_state(None, BasicOscilloscopeCtg.DIV_TIME, self._super_hint)
	
	@abstractmethod
	@enabledummy
	def set_offset_time(self, time_s:float):
		self.modify_state(self.get_offset_time, BasicOscilloscopeCtg.OFFSET_TIME, time_s)
		
	@abstractmethod
	@enabledummy
	def get_offset_time(self):
		return self.modify_state(None, BasicOscilloscopeCtg.OFFSET_TIME, self._super_hint)
	
	@abstractmethod
	@enabledummy
	def set_div_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_div_volt(channel), BasicOscilloscopeCtg.DIV_VOLT, volt_V, channel=channel)
		
	@abstractmethod
	@enabledummy
	def get_div_volt(self, channel:int):
		return self.modify_state(None, BasicOscilloscopeCtg.DIV_VOLT, self._super_hint, channel=channel)
	
	@abstractmethod
	@enabledummy
	def set_offset_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_offset_volt(channel), BasicOscilloscopeCtg.OFFSET_VOLT, volt_V, channel=channel)
		
	@abstractmethod
	@enabledummy
	def get_offset_volt(self, channel:int):
		return self.modify_state(None, BasicOscilloscopeCtg.OFFSET_VOLT, self._super_hint, channel=channel)
	
	@abstractmethod
	@enabledummy
	def set_chan_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_chan_enable(channel), BasicOscilloscopeCtg.CHAN_EN, enable, channel=channel)
		
	@abstractmethod
	@enabledummy
	def get_chan_enable(self, channel:int):
		return self.modify_state(None, BasicOscilloscopeCtg.CHAN_EN, self._super_hint, channel=channel)
	
	@abstractmethod
	@enabledummy
	def get_waveform(self, channel:int):
		return self.modify_data_state(None, BasicOscilloscopeCtg.WAVEFORM, self._super_hint, channel=channel)
	
	def refresh_state(self):
		self.get_div_time()
		self.get_offset_time()
		for ch in range(1, self.max_channels+1):
			self.get_div_volt(ch)
			self.get_offset_volt(ch)
			self.get_chan_enable(ch)
	
	
	
	def apply_state(self, new_state:dict):
		self.set_div_time(new_state[BasicOscilloscopeCtg.DIV_TIME])
		self.set_offset_time(new_state[BasicOscilloscopeCtg.OFFSET_TIME])
		for ch in range(1, self.max_channels+1):
			self.set_div_volt(ch, new_state[BasicOscilloscopeCtg.DIV_VOLT][ch-1])
			self.set_offset_volt(ch, new_state[BasicOscilloscopeCtg.OFFSET_VOLT][ch-1])
			self.set_chan_enable(ch, new_state[BasicOscilloscopeCtg.CHAN_EN][ch-1])
		

# class RemoteBasicOscilloscopeCtg(RemoteInstrument, BasicOscilloscopeCtg):
	
# 	''' This class mirrors the function in BasicOscilloscopeCtg, but each function
# 	is decorated with RemoteFunction. This lets a T/C client create RemoteInstruments
# 	for this category of instrument using this class and callings its functions, rather
# 	than creating a RemoteInstrument object and always calling remote_call.'''
	
# 	def __init__(self, ca:ClientAgent, log:plf.LogPile, remote_id:str=None, remote_address:str=None):
# 		super().__init__(ca, log, remote_id=remote_id, remote_address=remote_address)
	
# 	@remotefunction
# 	def set_div_time(self, time_s:float):
# 		pass
	
# 	@remotefunction
# 	def get_div_time(self):
# 		pass
	
# 	@remotefunction
# 	def set_offset_time(self, time_s:float):
# 		pass
	
# 	@remotefunction
# 	def get_offset_time(self):
# 		pass
	
# 	@remotefunction
# 	def set_div_volt(self, channel:int, volt_V:float):
# 		pass
	
# 	@remotefunction
# 	def get_div_volt(self, channel:int):
# 		pass
	
# 	@remotefunction
# 	def set_offset_volt(self, channel:int, volt_V:float):
# 		pass
	
# 	@remotefunction
# 	def get_offset_volt(self, channel:int):
# 		pass
	
# 	@remotefunction
# 	def set_chan_enable(self, channel:int, enable:bool):
# 		pass
	
# 	@remotefunction
# 	def get_chan_enable(self, channel:int):
# 		pass
	
# 	@remotefunction
# 	def get_waveform(self, channel:int):
# 		pass
	
# 	@remotefunction
# 	def refresh_state(self):
# 		pass
	
# 	@remotefunction
# 	def apply_state(self, new_state:dict):
# 		pass
	
class StdOscilloscopeCtg(BasicOscilloscopeCtg):
	
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
	
