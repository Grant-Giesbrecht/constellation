from constellation.base import *
from constellation.networking.net_client import *

class BasicOscilloscopeChannelState(Packable):
	
	def __init__(self):
		self.div_volt = None
		self.offset_volt = None
		self.chan_en = None
	
	def set_manifest(self):
		self.manifest.append("div_volt")
		self.manifest.append("offset_volt")
		self.manifest.append("chan_en")

class BasicOscilloscopeState(InstrumentState):
	def __init__(self, log:plf.LogPile, first_channel:int, num_channels:int, ndiv_horiz, ndiv_vert):
		super().__init__(log=log)
		
		self.first_channel = first_channel
		self.num_channels = num_channels
		
		self.ndiv_horiz = ndiv_horiz
		self.ndiv_vert = ndiv_vert
		
		self.div_time = None
		self.offset_time = None
		self.channels = IndexedList(self.first_channel, self.num_channels, validate_type=BasicOscilloscopeChannelState)
		
		for ch_no in self.channels.get_range():
			self.channels.set_idx_val(ch_no, BasicOscilloscopeChannelState())
	
	def set_manifest(self):
		self.manifest.append("first_channel")
		self.manifest.append("num_channels")
		self.manifest.append("div_time")
		self.manifest.append("offset_time")
		self.obj_manifest.append("channels")

class BasicOscilloscopeCtg(Driver):
	
	WAVEFORM = "waveform"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn="", max_channels:int=1, num_div_horiz:int=10, num_div_vert:int=8, dummy:bool=False, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, relay=relay, **kwargs)
		
		self.max_channels = max_channels
		
		self.state = BasicOscilloscopeState(self.log, self.first_channel, self.max_channels, num_div_horiz, num_div_vert)
		
		self.data[BasicOscilloscopeCtg.WAVEFORM] = IndexedList(self.first_channel, self.max_channels, log=self.log)
		
		if self.dummy:
			self.init_dummy_state()
			self.print_state()
		
	def init_dummy_state(self) -> None:
		self.set_div_time(10e-3)
		print(f"div_time = {self.state.div_time}")
		self.set_offset_time(0)
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
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
		for channel in range(self.first_channel, self.first_channel+self.max_channels):
		
			ampl = 1 # V
			freq = 40*(channel+1) # Hz
			npoints = 101
			
			# Create time series
			t_span = self.state.get(["ndiv_horiz"]) * self.state.get(["div_time"])
			t_start = -1*t_span/2+self.state.get(["offset_time"])
			t_series = np.linspace(t_start, t_start + t_span, npoints)
			
			# Create waveform
			wave = ampl * np.sin(t_series*2*np.pi*freq)
			
			# Trim waveform to represent clipping on real scope
			v_span = self.state.get(["ndiv_vert"]) * self.state.get(["channels", "div_volt"], indices=[channel])
			v_min = -1*v_span/2+self.state.get(["channels", "offset_volt"], indices=[channel])
			v_max = v_min + v_span
			wave_clipped = [np.max([np.min([element, v_max]), v_min]) for element in wave]
			
			# Return result
			self.data[BasicOscilloscopeCtg.WAVEFORM].set_idx_val(channel, {"time_s":t_series, "volt_V":wave_clipped})
	
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
					rval = self.state.get(["div_time"])
				case "set_offset_time":
					rval = None
				case "get_offset_time":
					rval = self.state.get(["offset_time"])
				case "set_div_volt":
					rval = None
				case "get_div_volt":
					rval = self.state.get(["channels", "div_volt"], indices=[args[0]])
				case "set_offset_volt":
					rval = None
				case "get_offset_volt":
					rval = self.state.get(["channels", "offset_volt"], indices=[args[0]])
				case "set_chan_enable":
					rval = None
				case "get_chan_enable":
					rval = self.state.get(["channels", "chan_en"], indices=[args[0]])
				case "get_waveform":
					self.remake_dummy_waves()
					rval = self.data[BasicOscilloscopeCtg.WAVEFORM].get_idx_val(args[0])
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
				
			self.debug(f"Dummy responder sending >{protect_str(rval)}< to {adjective} function (>{func_name}<).")
			return rval
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	
	@abstractmethod
	def set_div_time(self, time_s:float):
		self.modify_state(self.get_div_time, ["div_time"], time_s)
	
	@abstractmethod
	@enabledummy
	def get_div_time(self):
		return self.modify_state(None, ["div_time"], self._super_hint)
	
	
	@abstractmethod
	def set_offset_time(self, time_s:float):
		self.modify_state(self.get_offset_time, ["offset_time"], time_s)
		
	@abstractmethod
	@enabledummy
	def get_offset_time(self):
		return self.modify_state(None, ["offset_time"], self._super_hint)
	
	@abstractmethod
	def set_div_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_div_volt(channel), ["channels", "div_volt"], volt_V, indices=[channel])
		
	@abstractmethod
	@enabledummy
	def get_div_volt(self, channel:int):
		return self.modify_state(None, ["channels", "div_volt"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_offset_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_offset_volt(channel), ["channels", "offset_volt"], volt_V, indices=[channel])
		
	@abstractmethod
	@enabledummy
	def get_offset_volt(self, channel:int):
		return self.modify_state(None, ["channels", "offset_volt"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_chan_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_chan_enable(channel), ["channels", "chan_en"], enable, indices=[channel])
		
	@abstractmethod
	@enabledummy
	def get_chan_enable(self, channel:int):
		return self.modify_state(None, ["channels", "chan_en"], self._super_hint, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_waveform(self, channel:int):
		return self.modify_data_state(None, BasicOscilloscopeCtg.WAVEFORM, self._super_hint, indices=[channel])
	
	def refresh_state(self):
		self.get_div_time()
		self.get_offset_time()
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_div_volt(ch)
			self.get_offset_volt(ch)
			self.get_chan_enable(ch)
	
	def apply_state(self):
		self.set_div_time(self.state.get(["div_time"]))
		self.set_offset_time(self.state.get(["offset_time"]))
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.set_div_volt(ch, self.state.get(["channels", "div_volt"], indices=[ch]))
			self.set_offset_volt(ch, self.state.get(["channels", "offset_volt"], indices=[ch]))
			self.set_chan_enable(ch, self.state.get(["channels", "chan_en"], indices=[ch]))
	
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
	
