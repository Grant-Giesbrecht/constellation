"""RIGOL’s 1000Z Series Digital Oscilloscope

https://beyondmeasure.rigoltech.com/acton/attachment/1579/f-0386/1/-/-/-/-/DS1000Z_Programming%20Guide_EN.pdf
"""

from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *

class RigolDS1000Z(Oscilloscope):

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=DirectSCPIRelay(), max_channels:int=4, **kwargs):
		super().__init__(address, log, relay=relay, expected_idn='RIGOL TECHNOLOGIES,DS10', max_channels=max_channels, num_div_horiz=12, num_div_vert=8, **kwargs)
		
		#TODO: Turn into Mixin
		# self.meas_table = {StdOscilloscopeCtg.MEAS_VMAX:'VMAX', StdOscilloscopeCtg.MEAS_VMIN:'VMIN', StdOscilloscopeCtg.MEAS_VAVG:'VAVG', StdOscilloscopeCtg.MEAS_VPP:'VPP', StdOscilloscopeCtg.MEAS_FREQ:'FREQ'}
		
		# self.stat_table = {StdOscilloscopeCtg.STAT_AVG:'AVER', StdOscilloscopeCtg.STAT_MAX:'MAX', StdOscilloscopeCtg.STAT_MIN:'MIN', StdOscilloscopeCtg.STAT_CURR:'CURR', StdOscilloscopeCtg.STAT_STD:'DEV'}
	
	# def set_div_time(self, time_s:float):
	# 	self.write(f":TIM:MAIN:SCAL {time_s}")
	# 	super().set_div_time(time_s)
	
	@superreturn
	def set_div_time(self, time_s:float):
		self.write(f":TIM:MAIN:SCAL {time_s}")
		
	@superreturn
	def get_div_time(self):
		self._super_hint = float(self.query(f":TIM:MAIN:SCAL?"))
	
	@superreturn
	def set_offset_time(self, time_s:float):
		self.write(f":TIM:MAIN:OFFS {time_s}")
	
	@superreturn
	def get_offset_time(self):
		self._super_hint = float(self.query(f":TIM:MAIN:OFFS?"))
	
	@superreturn
	def set_div_volt(self, channel:int, volt_V:float):
		self.write(f":CHAN{channel}:SCAL {volt_V}")
	
	@superreturn
	def get_div_volt(self, channel:int):
		self._super_hint = float(self.query(f":CHAN{channel}:SCAL?"))
	
	@superreturn
	def set_offset_volt(self, channel:int, volt_V:float):
		self.write(f":CHAN{channel}:OFFS {volt_V}")
	
	@superreturn
	def get_offset_volt(self, channel:int):
		self._super_hint = float(self.query(f":CHAN{channel}:OFFS?"))
	
	@superreturn
	def set_chan_enable(self, channel:int, enable:bool):
		self.write(f":CHAN{channel}:DISP {bool_to_str01(enable)}")
	
	@superreturn
	def get_chan_enable(self, channel:int):
		self._super_hint = str_to_bool(self.query(f":CHAN{channel}:DISP?"))
	
	@superreturn
	def set_probe_attenuation(self, channel:int, attenuation:float):
		valid_probe_attenuations = {0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000}
		if attenuation not in valid_probe_attenuations:
			self.error(f"Probe attenuation >{attenuation}< not supported. Valid options: {sorted(valid_probe_attenuations)}")
			return
		self.write(f":CHAN{channel}:PROB {attenuation}")
	
	@superreturn
	def get_probe_attenuation(self, channel:int):
		self._super_hint =  float(self.query(f":CHAN{channel}:PROB?"))
	
	@superreturn
	def set_bandwidth_limit(self, channel:int, enable:bool):
		enable_code = "OFF"
		if enable:
			enable_code = "20M"
		self.write(f":CHAN{channel}:BWL {enable_code}")
	
	@superreturn
	def get_bandwidth_limit(self, channel:int):
		resp = self.query(f":CHAN{channel}:BWL?")
		if resp is None:
			self._super_hint = None
			return
		self._super_hint = resp.strip().upper() in ["1", "ON", "20M"]
	
	@superreturn
	def set_trigger_mode(self, mode:str):
		
		mode_table = {Oscilloscope.TRIG_SINGLE:"SING", Oscilloscope.TRIG_AUTO:"AUTO", Oscilloscope.TRIG_NORM:"NORM"}
		if mode not in mode_table:
			self.error(f"Cannot set trigger mode >{mode}<. Mode not recognized.")
			return
		
		self.write(f":TRIG:EDGE:SWE {mode_table[mode]}")
	
	@superreturn
	def get_trigger_mode(self):
		
		# Get value from scope
		mode = self.query(":TRIG:EDGE:SWE?").strip()
		
		mode_table = {Oscilloscope.TRIG_SINGLE:"SING", Oscilloscope.TRIG_AUTO:"AUTO", Oscilloscope.TRIG_NORM:"NORM"}
		inverted = {v: k for k, v in mode_table.items()}
		
		if mode not in inverted:
			self.error(f"Cannot set trigger mode >{mode}<. Mode not recognized.")
			return
		self._super_hint = inverted[mode]
	
	@superreturn
	def set_trigger_level(self, level_V:float):
		self.write(f":TRIG:EDGE:LEV {level_V}")

	@superreturn
	def get_trigger_level(self):
		self._super_hint = self.query(f":TRIG:EDGE:LEV?")
	
	@superreturn
	def set_trigger_source(self, channel:int=None, external:bool=False, line:bool=False):
		
		# Get source string
		src_str = self._format_trigger_source(channel, external, line)
		
		self.write(f":TRIG:EDGE:SOUR {src_str}")
	
	@superreturn
	def get_trigger_source(self):
		
		src_str = self.query(f":TRIG:EDGE:SOUR?").strip()
		
		if src_str == "CHAN1":
			self._super_hint = "1"
		elif src_str == "CHAN2":
			self._super_hint = "2"
		elif src_str == "CHAN3":
			self._super_hint = "3"
		elif src_str == "CHAN4":
			self._super_hint = "4"
		elif src_str == "EXT":
			self._super_hint = "EXT"
		elif src_str == "AC":
			self._super_hint = "LINE"
		else:
			self.warning(f"Unrecognized trigger source string. >@LOCK{src_str}@UNLOCK<")
			self._super_hint = src_str
	
	@superreturn
	def run_acquisition(self):
		self.write(f":RUN")
	
	@superreturn
	def stop_acquisition(self):
		self.write(f":STOP")
	
	@superreturn
	def do_single_trigger(self):
		self.write(f":SING")
	
	@superreturn
	def do_force_trigger(self):
		self.write(f":TFORCE")
	
	@superreturn
	def get_waveform(self, channel:int):
		
		self.write(f"WAV:SOUR CHAN{channel}")  # Specify channel to read
		self.write("WAV:MODE NORM")  # Specify to read data displayed on screen
		self.write("WAV:FORM ASCII")  # Specify data format to ASCII
		data = self.query("WAV:DATA?")  # Request data
		
		if data is None:
			return {"time_s":[], "volt_V":[]}
		
		# Split string into ASCII voltage values
		volts = data[11:].split(",")
		
		volts = [float(v) for v in volts]
		
		# Get timing data
		xorigin = float(self.query("WAV:XOR?"))
		xincr = float(self.query("WAV:XINC?"))
		
		# Get time values
		t = list(xorigin + np.linspace(0, xincr * (len(volts) - 1), len(volts)))
		
		self._super_hint = {"time_s":t, "volt_V":volts, "channel":channel}
	
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
