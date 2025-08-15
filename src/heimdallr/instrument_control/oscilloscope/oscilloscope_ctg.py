from heimdallr.base import *
from heimdallr.networking.net_client import *

class BasicOscilloscopeCtg(Driver):
	
	DIV_TIME = "div-time[s]"
	OFFSET_TIME = "offset-time[s]"
	DIV_VOLT = "div-volt[V]"
	OFFSET_VOLT = "offset-volt[V]"
	CHAN_EN = "chan_en[bool]"
	NDIV_VERT = "num-div-vert[1]"
	NDIV_HORIZ = "num-div-horiz[1]"
	WAVEFORM = "waveform[V]"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn="", max_channels:int=1, num_div_horiz:int=10, num_div_vert:int=8, dummy:bool=False, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, relay=relay, **kwargs)
		
		self.max_channels = max_channels
		self.num_div_horiz = num_div_horiz
		self.num_div_vert = num_div_vert
		
		self.state[BasicOscilloscopeCtg.DIV_TIME] = None
		self.state[BasicOscilloscopeCtg.OFFSET_TIME] = None
		self.state[BasicOscilloscopeCtg.NDIV_HORIZ] = num_div_horiz
		self.state[BasicOscilloscopeCtg.NDIV_VERT] = num_div_vert
		self.state[BasicOscilloscopeCtg.DIV_VOLT] = ChannelList(self.max_channels, log=self.log)
		self.state[BasicOscilloscopeCtg.OFFSET_VOLT] = ChannelList(self.max_channels, log=self.log)
		self.state[BasicOscilloscopeCtg.CHAN_EN] = ChannelList(self.max_channels, log=self.log)
		
		self.data[BasicOscilloscopeCtg.WAVEFORM] = ChannelList(self.max_channels, log=self.log)
		
		if self.dummy:
			self.init_dummy_state()
		
	def init_dummy_state(self) -> None:
		self.set_div_time(10e-3)
		self.set_offset_time(0)
		for ch in range(self.max_channels):
			self.set_div_volt(ch, 1)
			self.set_offset_volt(ch, 0)
			self.set_chan_enable(ch, True)
		
		self.remake_dummy_waves()
	
	def remake_dummy_waves(self) ->  None:
		''' Re-generates spoofed waveforms for each channel that is as realistic as
		possible for the given instrument state. Saves the waveform to the internal
		data tracker dict. Should be called each time a time or voltage parameter has
		been changed and the waveform data is queried.
		
		Returns:
			None
		'''
		
		# Loop over all channels
		for channel in range(self.max_channels):
		
			ampl = 1 # V
			freq = 40*(channel+1) # Hz
			npoints = 101
			
			# Create time series
			t_span = self.state[BasicOscilloscopeCtg.NDIV_HORIZ] * self.state[BasicOscilloscopeCtg.DIV_TIME]
			t_start = -1*t_span/2+self.state[BasicOscilloscopeCtg.OFFSET_TIME]
			t_series = np.linspace(t_start, t_start + t_span, npoints)
			
			print(f"span = {t_span}")
			
			# Create waveform
			wave = ampl * np.sin(t_series*2*np.pi*freq)
			
			# Trim waveform to represent clipping on real scope
			v_span = self.state[BasicOscilloscopeCtg.NDIV_VERT] * self.state[BasicOscilloscopeCtg.DIV_VOLT].get_ch_val(channel)
			v_min = -1*v_span/2+self.state[BasicOscilloscopeCtg.OFFSET_VOLT].get_ch_val(channel)
			v_max = v_min + v_span
			wave_clipped = [np.max([np.min([element, v_max]), v_min]) for element in wave]
			
			# Return result
			self.data[BasicOscilloscopeCtg.WAVEFORM].set_ch_val(channel, {"time_s":t_series, "volt_V":wave_clipped})
	
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
					rval = self.state[BasicOscilloscopeCtg.CHAN_EN].get_ch_val(args[0])
				case "get_waveform":
					self.remake_dummy_waves()
					rval = self.data[BasicOscilloscopeCtg.WAVEFORM].get_ch_val(args[0])
				case _:
					found = False
				
			
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
	def set_div_time(self, time_s:float):
		self.modify_state(self.get_div_time, BasicOscilloscopeCtg.DIV_TIME, time_s)
	
	@abstractmethod
	@enabledummy
	def get_div_time(self):
		return self.modify_state(None, BasicOscilloscopeCtg.DIV_TIME, self._super_hint)
	
	@abstractmethod
	def set_offset_time(self, time_s:float):
		self.modify_state(self.get_offset_time, BasicOscilloscopeCtg.OFFSET_TIME, time_s)
		
	@abstractmethod
	@enabledummy
	def get_offset_time(self):
		return self.modify_state(None, BasicOscilloscopeCtg.OFFSET_TIME, self._super_hint)
	
	@abstractmethod
	def set_div_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_div_volt(channel), BasicOscilloscopeCtg.DIV_VOLT, volt_V, channel=channel)
		
	@abstractmethod
	@enabledummy
	def get_div_volt(self, channel:int):
		return self.modify_state(None, BasicOscilloscopeCtg.DIV_VOLT, self._super_hint, channel=channel)
	
	@abstractmethod
	def set_offset_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_offset_volt(channel), BasicOscilloscopeCtg.OFFSET_VOLT, volt_V, channel=channel)
		
	@abstractmethod
	@enabledummy
	def get_offset_volt(self, channel:int):
		return self.modify_state(None, BasicOscilloscopeCtg.OFFSET_VOLT, self._super_hint, channel=channel)
	
	@abstractmethod
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
		for ch in range(self.max_channels):
			self.get_div_volt(ch)
			self.get_offset_volt(ch)
			self.get_chan_enable(ch)
	
	def apply_state(self, new_state:dict):
		self.set_div_time(new_state[BasicOscilloscopeCtg.DIV_TIME])
		self.set_offset_time(new_state[BasicOscilloscopeCtg.OFFSET_TIME])
		for ch in range(self.max_channels):
			self.set_div_volt(ch, new_state[BasicOscilloscopeCtg.DIV_VOLT][ch-1])
			self.set_offset_volt(ch, new_state[BasicOscilloscopeCtg.OFFSET_VOLT][ch-1])
			self.set_chan_enable(ch, new_state[BasicOscilloscopeCtg.CHAN_EN][ch-1])
	
	def refresh_data(self):
		
		for ch in range(1, self.max_channels):
			self.get_waveform(ch)
			

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
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn="", dummy:bool=False, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, **kwargs)
	
	@abstractmethod
	def add_measurement(self):
		pass
	
	@abstractmethod
	def get_measurement(self):
		pass
	
	def refresh_state(self):
		super().refresh_state()
	
