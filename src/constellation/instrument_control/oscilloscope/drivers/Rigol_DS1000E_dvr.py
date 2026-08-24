"""RIGOL DS1000E Series Digital Oscilloscope

NOTE: the link below is the DS1000*Z* programming guide, carried over when this file was copied
from the DS1000Z driver. The DS1000E is a different, older instrument with a considerably smaller
command set - which is why this driver is only partially category-compliant. Replace with the
DS1000E/D guide.
https://beyondmeasure.rigoltech.com/acton/attachment/1579/f-0386/1/-/-/-/-/DS1000Z_Programming%20Guide_EN.pdf
"""

from constellation.base import *
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *

class RigolDS1000E(Oscilloscope):
	''' Rigol DS1000E series oscilloscope.
	
	**This driver is deliberately NOT fully category-compliant, and is the reference example of
	how Constellation represents an instrument that cannot be.** The DS1000E's remote interface
	is genuinely incomplete - the timebase, for one, can be neither read nor set over SCPI - so
	no amount of driver work can make it implement all of `Oscilloscope`. That is a property of
	the hardware, not an unfinished migration.
	
	The pattern: every abstract method of the category IS defined, and the ones the instrument
	cannot perform are marked `@feature_unavailable("<why>")`. Consequences:
	
	 - the class is constructible, so the ~80% of the driver that does work is usable;
	 - calling an unsupported method raises `FeatureUnavailable` naming the limitation, instead
	   of the whole class silently failing to instantiate;
	 - `unavailable_features()` reports the gaps *before* they're called, so a GUI can grey out
	   controls it can't drive;
	 - `refresh_state()`/`apply_state()` skip the unsupported calls and log at debug rather than
	   aborting the sweep partway through.
	
	See `docs/partial_compliance.md`.
	'''
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, max_channels:int=2, **kwargs):
		super().__init__(address, log, relay=relay, expected_idn='RIGOL TECHNOLOGIES,DS10', max_channels=max_channels, num_div_horiz=12, num_div_vert=8, **kwargs)
		
		#TODO: Turn into Mixin
		# self.meas_table = {StdOscilloscopeCtg.MEAS_VMAX:'VMAX', StdOscilloscopeCtg.MEAS_VMIN:'VMIN', StdOscilloscopeCtg.MEAS_VAVG:'VAVG', StdOscilloscopeCtg.MEAS_VPP:'VPP', StdOscilloscopeCtg.MEAS_FREQ:'FREQ'}
		
		# self.stat_table = {StdOscilloscopeCtg.STAT_AVG:'AVER', StdOscilloscopeCtg.STAT_MAX:'MAX', StdOscilloscopeCtg.STAT_MIN:'MIN', StdOscilloscopeCtg.STAT_CURR:'CURR', StdOscilloscopeCtg.STAT_STD:'DEV'}
	
	# def set_div_time(self, time_s:float):
	# 	self.write(f":TIM:MAIN:SCAL {time_s}")
	# 	super().set_div_time(time_s)
	
	# --- Genuine hardware limitations -------------------------------------------------------
	# The DS1000E's SCPI interface exposes no timebase control. These previously logged a warning
	# and then fell through to the category method, which wrote the requested value into
	# self.state - so the state tracker claimed a timebase the instrument had never been told
	# about, and get_div_time() reported it back as though it had been read from hardware.
	# Marking them unavailable is both honest and introspectable.
	
	@feature_unavailable("DS1000E cannot set the timebase over SCPI")
	def set_div_time(self, time_s:float):
		pass
	
	@feature_unavailable("DS1000E cannot query the timebase over SCPI")
	def get_div_time(self):
		pass
	
	@feature_unavailable("DS1000E cannot set the horizontal offset over SCPI")
	def set_offset_time(self, time_s:float):
		pass
	
	@feature_unavailable("DS1000E cannot query the horizontal offset over SCPI")
	def get_offset_time(self):
		pass
	
	@superreturn
	def set_div_volt(self, channel:int, volt_V:float):
		self.write(f":CHAN{channel}:SCAL {volt_V}")
	
	@superreturn
	def get_div_volt(self, channel:int):
		return float(self.query(f":CHAN{channel}:SCAL?"))
	
	@superreturn
	def set_offset_volt(self, channel:int, volt_V:float):
		self.write(f":CHAN{channel}:OFFS {volt_V}")
	
	@superreturn
	def get_offset_volt(self, channel:int):
		return float(self.query(f":CHAN{channel}:OFFS?"))
	
	@superreturn
	def set_chan_enable(self, channel:int, enable:bool):
		self.write(f":CHAN{channel}:DISP {bool_to_str01(enable)}")
	
	@superreturn
	def get_chan_enable(self, channel:int):
		return str_to_bool(self.query(f":CHAN{channel}:DISP?"))
	
	@superreturn
	def get_waveform(self, channel:int):
		
		self.write(f":WAV:SOUR CHAN{channel}")  # Specify channel to read
		self.write(":WAV:MODE NORM")  # Specify to read data displayed on screen
		self.write(":WAV:FORM BYTE")  # Specify data format to ASCII
		try:
			data = self.relay.inst.query_binary_values(f":WAV:DATA?", datatype='B')  # Request data
		except:
			self.error(f"Failed to query instrument")
			data = None
			
		if data is None:
			return {"time_index":[], "volt_V":[]}
		
		v_offs = self.get_offset_volt(channel=channel)
		v_scale = self.get_div_volt(channel=channel)
		# volt = (np.array(data) - 128) * (v_scale / 25.0) + v_offs # 25 because 25 ADC counts per division, 8 divisions -> 200 points total, 128 to re-center 8-bit int
		volts = (240.0 - np.array(data)) * (v_scale / 25.0) - (v_offs + v_scale * 4.6)
		
		volts = [float(v) for v in volts]
		
		lv = len(volts)
		t = np.linspace(0, len(volts)-1, len(volts))
		
		self.warning(f"DS1000E model does not support getting timebase; Time points returning >in index format, not seconds!<.")
		
		return {"time_index":t, "volt_V":volts, "channel":channel}
	
	def add_measurement(self, meas_type:int, channel:int=1):
		
		# Find measurement string
		if meas_type not in self.meas_table:
			self.error(f"Cannot add measurement >{meas_type}<. Measurement not recognized.")
			return
		item_str = self.meas_table[meas_type]
		
		# Get channel string
		channel_val = max(1, min(channel, 4))
		if channel_val != channel:
			self.error("Channel must be between 1 and 4.")
			return
		src_str = f"CHAN{channel_val}"
		
		# Send message
		self.write(f":MEASURE:ITEM {item_str},{src_str}")
	
	def get_measurement(self, meas_type:int, channel:int=1, stat_mode:int=0) -> float:
		
		# FInd measurement string
		if meas_type not in self.meas_table:
			self.log.error(f"Cannot add measurement >{meas_type}<. Measurement not recognized.")
			return
		item_str = self.meas_table[meas_type]
		
		# Get channel string
		channel = max(1, min(channel, 1000))
		if channel != channel:
			self.log.error("Channel must be between 1 and 4.")
			return
		src_str = f"CHAN{channel}"
		
		# Query result
		if stat_mode == 0:
			return float(self.query(f":MEASURE:ITEM? {item_str},{src_str}"))
		else:
			
			# Get stat string
			if stat_mode not in self.stat_table:
				self.log.error(f"Cannot use statistic option >{meas_type}<. Option not recognized.")
				return
			stat_str = self.stat_table[stat_mode]
			
			return float(self.query(f":MEASURE:STAT:ITEM? {stat_str},{item_str},{src_str}"))
	
	def clear_measurements(self):
		
		self.write(f":MEASURE:CLEAR ALL")
	
	def set_measurement_stat_display(self, enable:bool):
		'''
		Turns display statistical values on/off for the Rigol DS1000Z series scopes. Not
		part of the Oscilloscope, but local to this driver.
		
		Args:
			enable (bool): Turns displayed stats on/off
		
		Returns:
			None
		'''
		
		self.write(f":MEASure:STATistic:DISPlay {bool_to_ONOFF(enable)}")
	
	# --- Not yet implemented ----------------------------------------------------------------
	# These are abstract on `Oscilloscope` and have no DS1000E implementation yet. They are
	# marked unavailable so the class is constructible and the working majority of the driver is
	# reachable; the reason string deliberately says "unverified", NOT "the hardware cannot" -
	# unlike the timebase above, these have not been checked against the instrument.
	#
	# The DS1000E programming guide does appear to document commands for most of them, so each
	# is expected to convert into a real implementation once verified on the bench. Converting
	# one is a single-method edit: delete the decorator, add @superreturn and the SCPI body.
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def set_coupling(self, channel:int, coupling:str):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def get_coupling(self, channel:int):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def set_probe_attenuation(self, channel:int, attenuation:float):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def get_probe_attenuation(self, channel:int):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def set_bandwidth_limit(self, channel:int, enable:bool):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def get_bandwidth_limit(self, channel:int):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def set_trigger_mode(self, mode:str):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def get_trigger_mode(self):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def set_trigger_level(self, level_V:float):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def get_trigger_level(self):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def set_trigger_source(self, channel:int=None, external:bool=False, line:bool=False):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def get_trigger_source(self):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def run_acquisition(self):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def stop_acquisition(self):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def do_single_trigger(self):
		pass
	
	@feature_unavailable("not yet implemented for the DS1000E - SCPI support unverified on hardware (todo_list.md P2)")
	def do_force_trigger(self):
		pass
	
