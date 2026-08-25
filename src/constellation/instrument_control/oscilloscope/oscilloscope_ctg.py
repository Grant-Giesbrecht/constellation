
import pylogfile as plf
from abc import abstractmethod
from constellation.base import InstrumentState, IndexedList, CommandRelay, Driver, enabledummy, protect_str
import numpy as np
import inspect

import matplotlib.pyplot as plt
from matplotlib.gridspec import GridSpec

class OscilloscopeChannelState(InstrumentState):
	
	# __state_fields__ = (InstrumentState.__state_fields__+("div_volt", "offset_volt", "chan_en", "waveform"))
	__state_fields__ = ("div_volt", "offset_volt", "chan_en", "attenuation", "bw_limit", "waveform", "coupling")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("div_volt", unit="V")
		self.add_param("offset_volt", unit="V")
		self.add_param("chan_en", unit="bool")
		self.add_param("attenuation", unit="")
		self.add_param("bw_limit", unit="bool")
		self.add_param("coupling", unit="")
		
		self.add_param("waveform", unit="", is_data=True, value={"time_S":[], "volt_V":[]})

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

class Oscilloscope(Driver):
	
	TRIG_NORM = "trig-normal"
	TRIG_SINGLE = "trig-single"
	TRIG_AUTO = "trig-auto"
	
	COUPLING_AC = "coup-ac"
	COUPLING_DC = "coup-dc"
	COUPLING_GND = "coup-gnd"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn="", first_channel:int=1, max_channels:int=1, num_div_horiz:int=10, num_div_vert:int=8, dummy:bool=False, **kwargs):
		
		_state = OscilloscopeState(first_channel, max_channels, num_div_horiz, num_div_vert, log=log)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy, first_channel_num=first_channel, **kwargs)
		
		self.max_channels = max_channels #TODO: Replace with state
		
		if self.dummy:
			self.init_dummy_state()
		
	def init_dummy_state(self) -> None:
		self.set_div_time(10e-3)
		self.set_offset_time(0)
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.set_div_volt(ch, 1)
			self.set_offset_volt(ch, 0)
			self.set_chan_enable(ch, True)
			self.set_coupling(ch, self.COUPLING_DC)
		
		self.remake_dummy_waves()
	
	def remake_dummy_waves(self) ->  None:
		''' Re-generates spoofed waveforms for each channel that is as realistic as
		possible for the given instrument state. Saves the waveform to the internal
		data tracker dict. Should be called each time a time or voltage parameter has
		been changed and the waveform data is queried.
		
		Returns:
			None
		'''
		
		#TODO: Consider coupliing AC vs DC
		
		# Loop over all channels
		for channel in range(self.first_channel, self.first_channel+self.max_channels):
		
			ampl = 1 # V
			freq = 40*(channel+1) # Hz
			npoints = 101
			
			# Create time series.
			# A partially-compliant scope may have no timebase at all - RigolDS1000E cannot read
			# or set it over SCPI, so set_div_time() is marked @feature_unavailable, is skipped
			# during init_dummy_state(), and div_time/offset_time stay None. Fall back to a
			# nominal timebase so dummy mode still yields a plausible waveform rather than
			# raising TypeError on None. (Such an instrument's real waveforms have no meaningful
			# time axis either - the DS1000E driver returns sample index instead of seconds.)
			div_time = self.state.get(["div_time"])
			offset_time = self.state.get(["offset_time"])
			if div_time is None:
				div_time = 1e-3
			if offset_time is None:
				offset_time = 0.0
			
			t_span = self.state.get(["ndiv_horiz"]) * div_time
			t_start = -1*t_span/2 + offset_time
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
		''' Supplies SYNTHETIC dummy values - things dummy mode has to invent because they are
		not already tracked in self.state.
		
		Plain set_*/get_* methods are deliberately absent: modify_state() handles those
		generically in dummy mode (setters store the value, getters read it back), so they need
		no entry here and no @enabledummy decorator. Only add a case when dummy mode must
		fabricate data.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			match func_name:
				case "get_waveform":
					self.remake_dummy_waves()
					rval = self.state.channels[args[0]].waveform
				case "run_acquisition" | "stop_acquisition" | "do_single_trigger" | "do_force_trigger":
					# Pure hardware actions with no state to track - nothing to simulate.
					rval = None
				case _:
					# Anything else reaching here means a method is decorated @enabledummy but
					# has no synthetic behavior defined - almost always it should simply not be
					# decorated at all. Fall back to the base Driver convention.
					return super().dummy_responder(func_name, *args, **kwargs)
			
			self.debug(f"Dummy responder sending >{protect_str(rval)}< to synthetic function (>{func_name}<).")
			return rval
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	@abstractmethod
	def set_coupling(self, channel:int, coupling:str):
		self.modify_state(lambda: self.get_coupling(channel), ["channels", "coupling"], coupling, indices=[channel])
	
	@abstractmethod
	def get_coupling(self, channel:int):
		return self.modify_state(None, ["channels", "coupling"], self._super_hint, indices=[channel])

	@abstractmethod
	def set_div_time(self, time_s:float):
		self.modify_state(self.get_div_time, ["div_time"], time_s)
	
	@abstractmethod
	def get_div_time(self):
		return self.modify_state(None, ["div_time"], self._super_hint)
	
	@abstractmethod
	def set_offset_time(self, time_s:float):
		self.modify_state(self.get_offset_time, ["offset_time"], time_s)
		
	@abstractmethod
	def get_offset_time(self):
		return self.modify_state(None, ["offset_time"], self._super_hint)
	
	@abstractmethod
	def set_div_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_div_volt(channel), ["channels", "div_volt"], volt_V, indices=[channel])
		
	@abstractmethod
	def get_div_volt(self, channel:int):
		return self.modify_state(None, ["channels", "div_volt"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_offset_volt(self, channel:int, volt_V:float):
		self.modify_state(lambda: self.get_offset_volt(channel), ["channels", "offset_volt"], volt_V, indices=[channel])
		
	@abstractmethod
	def get_offset_volt(self, channel:int):
		return self.modify_state(None, ["channels", "offset_volt"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_chan_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_chan_enable(channel), ["channels", "chan_en"], enable, indices=[channel])
		
	@abstractmethod
	def get_chan_enable(self, channel:int):
		return self.modify_state(None, ["channels", "chan_en"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_probe_attenuation(self, channel:int, attenuation:float):
		self.modify_state(lambda: self.get_probe_attenuation(channel), ["channels", "attenuation"], attenuation, indices=[channel])
	
	@abstractmethod
	def get_probe_attenuation(self, channel:int):
		return self.modify_state(None, ["channels", "attenuation"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_bandwidth_limit(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_bandwidth_limit(channel), ["channels", "bw_limit"], enable, indices=[channel])
	
	@abstractmethod
	def get_bandwidth_limit(self, channel:int):
		return self.modify_state(None, ["channels", "bw_limit"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_trigger_mode(self, mode:str):
		self.modify_state(lambda: self.get_trigger_mode(), ["trigger_mode"], mode)
	
	@abstractmethod
	def get_trigger_mode(self):
		return self.modify_state(None, ["trigger_mode"], self._super_hint)
	
	@abstractmethod
	def set_trigger_level(self, level_V:float):
		self.modify_state(lambda: self.get_trigger_level(), ["trigger_level"], level_V)

	@abstractmethod
	def get_trigger_level(self):
		return self.modify_state(None, ["trigger_level"], self._super_hint)
	
	def _parse_trigger_source(self, src_str:str):
		''' Inverse of _format_trigger_source(): turns a stored source string back into the
		(channel, external, line) keyword arguments set_trigger_source() expects. Needed by
		apply_state(), which only has the stored string to work from.
		
		Returns:
			dict: kwargs for set_trigger_source(), or None if the string isn't recognized.
		'''
		
		if src_str is None:
			return None
		
		src_str = str(src_str).strip().upper()
		
		if src_str.startswith("CHAN"):
			try:
				return {"channel": int(src_str[4:])}
			except ValueError:
				self.warning(f"Cannot parse trigger source >{src_str}<, unreadable channel number.")
				return None
		elif src_str == "EXT":
			return {"external": True}
		elif src_str == "AC":
			return {"line": True}
		
		self.warning(f"Cannot parse unrecognized trigger source >{src_str}<.")
		return None
	
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
	def set_trigger_source(self, channel:int=None, external:bool=False, line:bool=False):
		
		# Get source string
		src_str = self._format_trigger_source(channel, external, line)
		
		self.modify_state(lambda: self.get_trigger_source(), ["trigger_source"], src_str)
	
	@abstractmethod
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
	def get_waveform(self, channel:int, **kwargs):
		''' **kwargs absorbs driver-specific capture options (e.g. binary/full_memory/max_points
		on RigolDS1000Z) so drivers can extend get_waveform()'s options without requiring a
		matching category-level signature change - superreturn's wrapper calls this with
		whatever args/kwargs the driver-level call received. '''
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
		
		self.refresh_mixins()
	
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
		trig_kwargs = self._parse_trigger_source(self.state.trigger_source)
		if trig_kwargs is not None:
			self.set_trigger_source(**trig_kwargs)
		self.set_trigger_level(self.state.trigger_level)
		
		self.apply_mixins()
		
	def _begin_waveform_batch(self, **kwargs):
		''' Optional hook: called once by get_all_waveforms() before reading any channel, so a
		driver that needs to do something expensive/disruptive per full-record read (e.g.
		stopping acquisition) can do it ONCE for the whole batch instead of once per channel.
		Default no-op. Returns arbitrary state to pass to _end_waveform_batch(). '''
		return None

	def _end_waveform_batch(self, batch_state, **kwargs):
		''' Optional hook: called once by get_all_waveforms() after every channel has been read,
		undoing whatever _begin_waveform_batch() did (e.g. resuming acquisition). Default no-op. '''
		pass

	def get_all_waveforms(self, **kwargs):
		''' Returns a list of returend waveforms, one for each online channel.

		**kwargs is forwarded to get_waveform() as-is (e.g. binary=/full_memory=/max_points= on
		RigolDS1000Z), so callers can request e.g. get_all_waveforms(binary=False) the same way
		they'd call get_waveform(ch, binary=False). '''

		batch_state = self._begin_waveform_batch(**kwargs)
		try:
			# _skip_run_management is an opt-in hint for drivers that manage acquisition per
			# batch (RigolDS1000Z). Drivers whose get_waveform() takes no **kwargs - e.g.
			# RigolDS1000E - would raise TypeError on it, which superreturn swallows, silently
			# turning every waveform into None. Only send it where it's accepted.
			batch_kwargs = dict(kwargs)
			if self._get_waveform_accepts_batch_hint():
				batch_kwargs["_skip_run_management"] = True

			waveform_list = []
			for ch in range(self.state.first_channel, self.state.num_channels+self.state.first_channel):
				if self.get_chan_enable(ch):
					waveform_list.append(self.get_waveform(ch, **batch_kwargs))

			return waveform_list
		finally:
			self._end_waveform_batch(batch_state, **kwargs)

	def _get_waveform_accepts_batch_hint(self) -> bool:
		''' True if this driver's get_waveform() can accept the _skip_run_management hint,
		either by naming it or by absorbing it via **kwargs. '''

		try:
			# Unwrap the superreturn descriptor to inspect the driver's own function
			raw = getattr(type(self), "get_waveform")
			func = getattr(raw, "func", raw)
			params = inspect.signature(func).parameters
		except (TypeError, ValueError):
			return False

		if "_skip_run_management" in params:
			return True
		return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())
	
	def refresh_data(self):
		_ = self.get_all_waveforms()

class OscilloscopeMeasurementSetting(InstrumentState):
	
	# last_measured_value was registered with add_param() but missing here, so it silently did
	# not serialize - a saved measurement came back without its value. Found by the automatic
	# validate() hook (InstrumentState.__init_subclass__); this class never called validate().
	__state_fields__ = ("measurement_type", "measurement_source", "last_measured_value")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("measurement_type", unit="", value="") # Like VPP, etc
		self.add_param("measurement_source", unit="", value="") # Will save as chan1, chan2, etc
		
		self.add_param("last_measured_value", unit="", value="") # Last recorded value

class OscilloscopeMeasurementMixinState(InstrumentState):
	
	__state_fields__ = ("active_measurements", "show_stat_table")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("active_measurements", unit="", value=IndexedList(first_index=0, num_indices=10, validate_type=OscilloscopeMeasurementSetting, log=log))
		self.add_param("show_stat_table", unit="bool", value=False)

class MeasurementsMixin:
	
	__state_key__ = "measurements"
	__state_fragment__ = OscilloscopeMeasurementMixinState
	
	MEAS_VMAX = "meas-vmax"
	MEAS_VMIN = "meas-vmin"
	MEAS_VAVG = "meas-vavg"
	MEAS_VPP = "meas-vpp"
	MEAS_FREQ = "meas-freq"
	
	STAT_AVG = "stat-avg"
	STAT_MAX = "stat-max"
	STAT_MIN = "stat-min"
	STAT_CURR = "stat-curr"
	STAT_STD = "stat-std"
	
	# TODO: How to handle enabledummy with mixins?
	@abstractmethod
	def clear_measurements(self):
		self.state.state_fragments[self.__state_key__].active_measurements.clear()
		# self.modify_state(None, ["active_measurements"], [], fragment=self.__state_key__)
	
	@abstractmethod
	def add_measurement(self, channel:int, measurement:str) -> bool:
		
		# This is an example where modify_state is too general to handle the situation,
		# so instead I have to manually perform the state update logic.
		
		#TODO: Handle dummy!
		
		# Create source string
		source = f"chan{channel}"
		
		# Check if measurement already exists
		for am in self.state.state_fragments[self.__state_key__].active_measurements:
			if am.measurement_type == measurement and am.measurement_source == source:
				self.log.warning(f"Not adding measurement:>{measurement}< to source:>:a{source}<. Measurement already exists.")
				return False
		
		# Add measurement
		nm = OscilloscopeMeasurementSetting(self.log)
		nm.measurement_type = measurement
		nm.measurement_source = source
		self.state.state_fragments[self.__state_key__].active_measurements.append(nm)
		
		return True
	
	@abstractmethod
	def get_measurement(self, channel:int, measurement:str, stat_mode:str=STAT_CURR) -> float:
		''' Returns measurement result. REturns None if error occurs.
		'''
		
		# Create source string
		source = f"chan{channel}"
		
		# Find the matching active measurement. populated_items() is required here rather than
		# enumerate(): iterating an IndexedList skips unpopulated slots, so enumerate's counter
		# is a *positional* index, not the IndexedList key needed to write the result back.
		meas_idx = None
		for idx, am in self.state.state_fragments[self.__state_key__].active_measurements.populated_items():
			if am.measurement_type == measurement and am.measurement_source == source:
				meas_idx = idx
				break
		
		# Check if index was found
		if meas_idx is None:
			self.warning(f"Cannot read measurement:>{measurement}< on source:>:a{source}<. Measurement has not been added.")
			return None
		
		# A measurement value is synthetic in dummy mode - there is no state field holding it to
		# read back, so it has to be invented from the dummy waveform.
		if self.dummy:
			value = self._dummy_measurement(channel, measurement)
		else:
			value = self._super_hint
		
		# Update last measured value
		self.state.state_fragments[self.__state_key__].active_measurements[meas_idx].last_measured_value = value
		
		return value
	
	def _dummy_measurement(self, channel:int, measurement:str):
		''' Computes a measurement from the driver's own dummy waveform, so dummy mode returns
		numbers that are actually consistent with the waveform get_waveform() would return
		(rather than a fixed sentinel). Returns None if the measurement type isn't supported or
		no dummy waveform is available. '''
		
		# Only meaningful on a driver that generates dummy waveforms (i.e. an Oscilloscope)
		if not hasattr(self, "remake_dummy_waves"):
			return None
		
		self.remake_dummy_waves()
		
		try:
			wave = self.state.channels[channel].waveform
			volts = list(wave["volt_V"])
			times = list(wave["time_s"])
		except Exception as e:
			self.warning(f"Cannot synthesize dummy measurement, no usable waveform on channel >{channel}<. ({e})")
			return None
		
		if len(volts) == 0:
			return None
		
		match measurement:
			case MeasurementsMixin.MEAS_VMAX:
				return float(np.max(volts))
			case MeasurementsMixin.MEAS_VMIN:
				return float(np.min(volts))
			case MeasurementsMixin.MEAS_VPP:
				return float(np.max(volts) - np.min(volts))
			case MeasurementsMixin.MEAS_VAVG:
				return float(np.mean(volts))
			case MeasurementsMixin.MEAS_FREQ:
				# Count rising zero-crossings of the mean-subtracted wave over the captured span
				if len(times) < 2:
					return None
				centered = np.array(volts) - np.mean(volts)
				crossings = np.sum((centered[:-1] < 0) & (centered[1:] >= 0))
				span = times[-1] - times[0]
				return float(crossings / span) if span > 0 else None
			case _:
				self.warning(f"No dummy synthesis for measurement type >{measurement}<.")
				return None
		
	
	@abstractmethod
	def set_measurement_stat_display(self, enable:bool):
		'''
		Turns display statistical values on/off for the Rigol DS1000Z series scopes. Not
		part of the Oscilloscope, but local to this driver.
		
		Args:
			enable (bool): Turns displayed stats on/off
		
		Returns:
			None
		'''
		self.modify_state(lambda: self.get_measurement_stat_display(), ["show_stat_table"], enable, fragment=self.__state_key__)
	
	@abstractmethod
	def get_measurement_stat_display(self):
		'''
		Checks if the stats table is on or off.
		'''
		return self.modify_state(None, ["show_stat_table"], self._super_hint, fragment=self.__state_key__)
	
	def refresh_state(self):
		pass
		#TODO: How to get list of all active measurements?
	
	def apply_state(self):
		pass

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
	

_DEFAULT_CHAN_COLORS = {
	1: (0.925, 0.84, 0),
	2: (0, 159/255, 185/255),
	3: (204/255, 0, 175/255),
	4: (22/255, 0, 184/255),
}

def _waveform_label(wav, label):
	''' Determines the legend/title label for a single waveform dict, applying the same
	fallback rule plot_waveform() has always used. '''

	if label is not None:
		return label
	try:
		return f"Chan-{wav['channel']}"
	except (KeyError, TypeError):
		return "Unspecified Channel"

def _waveform_style(wav, osc, kwargs):
	''' Resolves the plot() kwargs for a single waveform dict: user-provided kwargs take
	priority, then the driver's own per-channel color (if osc is given), then the module
	default per-channel colors. '''

	plot_kwargs = {'linestyle': ':', 'marker': '.'}
	plot_kwargs.update(kwargs)
	if 'color' not in plot_kwargs:
		chan_color = None
		if osc is not None:
			try:
				chan_color = osc.state.channel_colors[wav['channel']]
			except Exception:
				pass
		if chan_color is None:
			try:
				chan_color = _DEFAULT_CHAN_COLORS.get(wav['channel'])
			except Exception:
				pass
		if chan_color is not None:
			plot_kwargs['color'] = chan_color
	return plot_kwargs

def _waveform_xdata(wav):
	''' Returns (x_data, x_unit) for a single waveform dict. '''

	if 'time_s' in wav:
		return wav['time_s'], "s"
	elif 'time_idx' in wav:
		return wav['time_idx'], "idx"
	return None, ""

def plot_waveform(waveform, axis=None, fig=None, osc:Oscilloscope=None, label=None, separateaxes=False, **kwargs):
	''' Plots a waveform dictionary. If multiple waveforms are provided (list
	of dicts), each will by default be plotted on the same axes.

	Args:
		waveform:     dict with keys 'time_s', 'volt_V', and optionally 'channel',
		              or a list of such dicts.
		axis:         matplotlib Axes to plot on. If None, one is created. Must not be given
		              when separateaxes=True - there is no single axis to plot onto in that mode.
		fig:          matplotlib Figure to use when axis is None. If None, a new figure is created.
		osc:          Oscilloscope driver; used to look up per-channel colors from its state.
		label:        Label string forwarded to plot(). Overrides the auto-generated channel
		              label for every waveform, so it's only useful with a single waveform.
		separateaxes: If True, each waveform gets its own stacked subplot (one row per waveform)
		              with a shared, synced X axis (panning/zooming one moves all of them)
		              instead of being layered on one set of axes. Returns a list of Axes (one
		              per waveform, same order as the input) instead of a single Axes.
		**kwargs:     Forwarded to matplotlib plot() (color, marker, linestyle, alpha, etc.).
		              Providing 'color' here overrides any channel color lookup.

	Returns:
		A single matplotlib Axes (separateaxes=False, the default), or a list of Axes, one per
		waveform (separateaxes=True).
	'''

	# Normalize input to list
	if isinstance(waveform, dict):
		waveforms = [waveform]
	elif isinstance(waveform, list):
		waveforms = waveform
	else:
		raise TypeError(f"waveform must be a dict or list of dicts, got {type(waveform)}")

	if separateaxes:

		if axis is not None:
			raise ValueError("Cannot pass both axis and separateaxes=True - there is no single axis to plot onto in that mode.")

		if fig is None:
			fig = plt.figure()

		# Explicit grid-based layout (GridSpec + add_subplot per cell) rather than
		# Figure.subplots(), for compatibility with downstream plotting tooling.
		gs = GridSpec(len(waveforms), 1, figure=fig)
		axes = []
		for i in range(len(waveforms)):
			ax = fig.add_subplot(gs[i, 0], sharex=axes[0] if axes else None)
			axes.append(ax)

		x_unit = ""
		for wav, ax in zip(waveforms, axes):

			plot_label = _waveform_label(wav, label)
			plot_kwargs = _waveform_style(wav, osc, kwargs)
			x, x_unit = _waveform_xdata(wav)

			ax.plot(x, wav['volt_V'], label=plot_label, **plot_kwargs)
			ax.set_title(plot_label)
			ax.grid(True)
			ax.set_ylabel("Voltage (V)")
			ax.label_outer()  # hide redundant x tick labels on all but the bottom axis

		axes[-1].set_xlabel(f"Time ({x_unit})")

		return axes

	# Get or create axes
	if axis is None:
		if fig is None:
			fig = plt.figure()
		if not fig.get_axes():
			axis = fig.add_subplot(1, 1, 1)
		else:
			axis = fig.gca()

	x_unit = ""

	for wav in waveforms:

		plot_label = _waveform_label(wav, label)
		plot_kwargs = _waveform_style(wav, osc, kwargs)
		x, x_unit = _waveform_xdata(wav)

		axis.plot(x, wav['volt_V'], label=plot_label, **plot_kwargs)

	if len(waveforms) > 1:
		axis.legend()

	axis.set_xlabel(f"Time ({x_unit})")
	axis.grid(True)
	axis.set_ylabel("Voltage (V)")
	
	return axis
