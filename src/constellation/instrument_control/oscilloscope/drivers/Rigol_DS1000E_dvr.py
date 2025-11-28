"""RIGOL’s 1000Z Series Digital Oscilloscope

https://beyondmeasure.rigoltech.com/acton/attachment/1579/f-0386/1/-/-/-/-/DS1000Z_Programming%20Guide_EN.pdf
"""

from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *

class RigolDS1000E(Oscilloscope):

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=DirectSCPIRelay(), max_channels:int=2, **kwargs):
		super().__init__(address, log, relay=relay, expected_idn='RIGOL TECHNOLOGIES,DS10', max_channels=max_channels, num_div_horiz=12, num_div_vert=8, **kwargs)
		
		#TODO: Turn into Mixin
		# self.meas_table = {StdOscilloscopeCtg.MEAS_VMAX:'VMAX', StdOscilloscopeCtg.MEAS_VMIN:'VMIN', StdOscilloscopeCtg.MEAS_VAVG:'VAVG', StdOscilloscopeCtg.MEAS_VPP:'VPP', StdOscilloscopeCtg.MEAS_FREQ:'FREQ'}
		
		# self.stat_table = {StdOscilloscopeCtg.STAT_AVG:'AVER', StdOscilloscopeCtg.STAT_MAX:'MAX', StdOscilloscopeCtg.STAT_MIN:'MIN', StdOscilloscopeCtg.STAT_CURR:'CURR', StdOscilloscopeCtg.STAT_STD:'DEV'}
	
	# def set_div_time(self, time_s:float):
	# 	self.write(f":TIM:MAIN:SCAL {time_s}")
	# 	super().set_div_time(time_s)
	
	@superreturn
	def set_div_time(self, time_s:float):
		self.warning(f"DS1000E model does not support setting timebase remotely.")
		
	@superreturn
	def get_div_time(self):
		self.warning(f"DS1000E model does not support querying timebase remotely.")
		self._super_hint = None
	
	@superreturn
	def set_offset_time(self, time_s:float):
		self.warning(f"DS1000E model does not support setting timebase remotely.")
	
	@superreturn
	def get_offset_time(self):
		self.warning(f"DS1000E model does not support querying timebase remotely.")
		self._super_hint = None
	
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
		
		self._super_hint = {"time_index":t, "volt_V":volts}
	
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