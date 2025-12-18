from constellation.base import *
from constellation.networking.net_client import *

import matplotlib.pyplot as plt

class OscilloscopeChannelState(InstrumentState):
	
	# __state_fields__ = (InstrumentState.__state_fields__+("div_volt", "offset_volt", "chan_en", "waveform"))
	__state_fields__ = ("div_volt", "offset_volt", "chan_en", "attenuation", "bw_limit", "waveform")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("div_volt", unit="V")
		self.add_param("offset_volt", unit="V")
		self.add_param("chan_en", unit="bool")
		self.add_param("attenuation", unit="")
		self.add_param("bw_limit", unit="bool")
		
		self.add_param("waveform", unit="", is_data=True, value={"time_S":[], "volt_V":[]})
		
		self.validate()

class OscilloscopeState(InstrumentState):
	
	# __state_fields__ = (InstrumentState.__state_fields__ + ("first_channel", "num_channels", "ndiv_horiz", "ndiv_vert", "div_time", "offset_time", "channels"))
	__state_fields__ = ("first_channel", "num_channels", "ndiv_horiz", "ndiv_vert", "div_time", "offset_time", "channels", "channel_colors", "trigger_source", "trigger_mode", "trigger_level")
	
	def __init__(self, first_channel:int, num_channels:int, ndiv_horiz, ndiv_vert, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		# self.add_param("channel_colors", unit="", value={1:(1, 1, 0.21), 2:(0, 0.78, 0.91), 3:(1, 0.36, 0.88), 4:(0.09, 0, 0.72)})
		self.add_param("channel_colors", unit="", value={1:(0.925, 0.84, 0), 2:(0, 159/255, 185/255), 3:(204/255, 0, 175/255), 4:(22/255, 0, 184/255)})
		
		self.add_param("ndiv_horiz", unit="1", value=ndiv_horiz)
		self.add_param("ndiv_vert", unit="1", value=ndiv_vert)
		
		self.add_param("div_time", unit="s")
		self.add_param("offset_time", unit="s")
		
		self.add_param("trigger_source", unit="")
		self.add_param("trigger_mode", unit="")
		self.add_param("trigger_level", unit="")
		
		self.add_param("channels", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=OscilloscopeChannelState, log=log))
		
		for ch_no in self.channels.get_range():
			self.channels[ch_no] = OscilloscopeChannelState(log=log)
		
		self.validate()

class Oscilloscope(Driver):
	
	TRIG_NORM = "trig-normal"
	TRIG_SINGLE = "trig-single"
	TRIG_AUTO = "trig-auto"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn="", max_channels:int=1, num_div_horiz:int=10, num_div_vert:int=8, dummy:bool=False, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, relay=relay, **kwargs)
		
		self.max_channels = max_channels #TODO: Replace with state
		
		self.state = OscilloscopeState(self.first_channel, self.max_channels, num_div_horiz, num_div_vert, log=log)
		
		if self.dummy:
			self.init_dummy_state()
		
	def init_dummy_state(self) -> None:
		self.set_div_time(10e-3)
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
			self.state.channels[channel].waveform = {"time_s":t_series, "volt_V":wave_clipped}
	
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
					rval = self.state.channels[args[0]].waveform
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
	def set_probe_attenuation(self, channel:int, attenuation:float):
		self.modify_state(lambda: self.get_probe_attenuation(channel), ["channels", "attenuation"], attenuation, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_probe_attenuation(self, channel:int):
		return self.modify_state(None, ["channels", "attenuation"], self._super_hint, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def set_bandwidth_limit(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_bandwidth_limit(channel), ["channels", "bw_limit"], enable, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_bandwidth_limit(self, channel:int):
		return self.modify_state(None, ["channels", "bw_limit"], self._super_hint, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def set_trigger_mode(self, mode:str):
		self.modify_state(lambda: self.get_trigger_mode(), ["trigger_mode"], mode)
	
	@abstractmethod
	@enabledummy
	def get_trigger_mode(self):
		return self.modify_state(None, ["trigger_mode"], self._super_hint)
	
	@abstractmethod
	@enabledummy
	def set_trigger_level(self, level_V:float):
		self.modify_state(lambda: self.get_trigger_level(), ["trigger_level"], level_V)

	@abstractmethod
	@enabledummy
	def get_trigger_level(self):
		return self.modify_state(None, ["trigger_level"], self._super_hint)
	
	def _format_trigger_source(self, channel:int=None, external:bool=False, line:bool=False):
		''' Converts the three-input trigger source argument format into an approp.
		formatted string. Returns None on error
		'''
		
		if channel is not None:
			if channel < self.state.first_channel:
				return None
			if channel >= self.state.first_channel + self.state.num_channels:
				return None
			src_str = f"CHAN{channel}"
		elif external:
			src_str = "EXT"
		elif line:
			src_str = "AC"
		else: # No valid option was set, abort
			return None
		
		return src_str
	
	@abstractmethod
	@enabledummy
	def set_trigger_source(self, channel:int=None, external:bool=False, line:bool=False):
		
		# Get source string
		src_str = self._format_trigger_source(channel, external, line)
		
		self.modify_state(lambda: self.get_trigger_source(), ["trigger_source"], src_str)
	
	@abstractmethod
	@enabledummy
	def get_trigger_source(self):
		return self.modify_state(None, ["trigger_source"], self._super_hint)
	
	@abstractmethod
	@enabledummy
	def run_acquisition(self):
		pass
	
	@abstractmethod
	@enabledummy
	def stop_acquisition(self):
		pass
	
	@abstractmethod
	@enabledummy
	def do_single_trigger(self):
		pass
	
	@abstractmethod
	@enabledummy
	def do_force_trigger(self):
		pass
	
	# @abstractmethod
	# @enabledummy
	# def set_bandwidth_limit(self, channel:int, enable:bool):
	# 	return self.modify_state(lambda: self.get_bandwidth_limit(channel), ["channels", "bw_limit"], enable, indices=[channel])
	# 
	# @abstractmethod
	# @enabledummy
	# def get_bandwidth_limit(self, channel:int, attenuation:float):
	# 	return self.modify_state(None, ["channels", "bw_limit"], self._super_hint, indices=[channel])
		
	@abstractmethod
	@enabledummy
	def get_waveform(self, channel:int):
		return self.modify_state(None, ["channels", "waveform"], self._super_hint, indices=[channel])
	
	def refresh_state(self):
		self.get_div_time()
		self.get_offset_time()
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_div_volt(ch)
			self.get_offset_volt(ch)
			self.get_chan_enable(ch)
			self.get_bandwidth_limit(ch)
			self.get_probe_attenuation(ch)
		self.get_trigger_mode()
		self.get_trigger_level()
		self.get_trigger_source()
	
	def apply_state(self):
		self.set_div_time(self.state.get(["div_time"]))
		self.set_offset_time(self.state.get(["offset_time"]))
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.set_div_volt(ch, self.state.get(["channels", "div_volt"], indices=[ch]))
			self.set_offset_volt(ch, self.state.get(["channels", "offset_volt"], indices=[ch]))
			self.set_chan_enable(ch, self.state.get(["channels", "chan_en"], indices=[ch]))
			self.set_bandwidth_limit(ch, self.state.get(["channels", "bw_limit"], indices=[ch]))
			self.set_probe_attenuation(ch, self.state.get(["channels", "attenuation"], indices=[ch]))
		self.set_trigger_mode(self.state.trigger_mode)
		self.set_trigger_source(self.state.trigger_source)
		self.set_trigger_level(self.state.trigger_level)
		
	def get_all_waveforms(self):
		''' Returns a list of returend waveforms, one for each online channel '''
		
		waveform_list = []
		for ch in range(self.state.first_channel, self.state.num_channels+self.state.first_channel):
			if self.get_chan_enable(ch):
				waveform_list.append(self.get_waveform(ch))
		
		return waveform_list
	
	def refresh_data(self):
		_ = self.get_all_waveforms()


#TODO: replace with mixin	
# class StdOscilloscopeCtg(Oscilloscope):
	
# 	# Measurement options
# 	MEAS_VMAX = 0
# 	MEAS_VMIN = 1
# 	MEAS_VAVG = 2
# 	MEAS_VPP  = 3
# 	MEAS_FREQ = 4
	
# 	# Statistics options for measurement options
# 	STAT_NONE = 0
# 	STAT_AVG = 1
# 	STAT_MAX = 2
# 	STAT_MIN = 3
# 	STAT_CURR = 4
# 	STAT_STD = 5
	
# 	def __init__(self, address:str, log:plf.LogPile, expected_idn="", dummy:bool=False, **kwargs):
# 		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, **kwargs)
	
# 	@abstractmethod
# 	def add_measurement(self):
# 		pass
	
# 	@abstractmethod
# 	def get_measurement(self):
# 		pass
	
# 	def refresh_state(self):
# 		super().refresh_state()
	

def plot_waveform(waveform, axis=None, figno:int=1, osc:Oscilloscope=None):
	''' Plots a waveform dictionary. If multiple waveform are provided (list
	of dicts), each will be plotted on the same axes.'''
	
	# Get wavefomr list from dict/list input
	if isinstance(waveform, dict):
		waveforms = [waveform]
	elif isinstance(waveform, list):
		waveforms = waveform
	else:
		raise TypeError
	
	# If axis is not provided, create one
	if axis is None:
		figN = plt.figure(figno)
		gsN = figN.add_gridspec(1, 1)
		axis = figN.add_subplot(gsN[0, 0])
	
	# Iterate over all waveforms
	for wav in waveforms:
		
		# Get channel label
		ch_label = "Unspecified Channel"
		try:
			ch = wav['channel']
			ch_label = f"Chan-{ch}"
		except:
			pass
		
		# Get channel color if specified by driver object
		chan_color = None
		if osc is not None:
			try:
				chan_color = osc.state.channel_colors[wav['channel']]
			except Exception as e:
				chan_color = None
		
		# Get x-parameter from waveform
		x = None
		if 'time_s' in wav:
			x = wav['time_s']
			x_unit = "s"
		elif 'time_idx' in wav:
			x = wav['time_idx']
			x_unit = "idx"
		
		# Plot result
		if chan_color is None:
			axis.plot(x, wav['volt_V'], linestyle=':', marker='.', label=ch_label)
		else:
			axis.plot(x, wav['volt_V'], linestyle=':', marker='.', color=chan_color, label=ch_label)
	
	if len(waveforms) > 1:
		axis.legend()
	
	axis.set_xlabel(x_unit)
	axis.grid(True)
	axis.set_ylabel("Voltage (V)")
	
	return axis