"""RIGOL’s 1000Z Series Digital Oscilloscope

https://beyondmeasure.rigoltech.com/acton/attachment/1579/f-0386/1/-/-/-/-/DS1000Z_Programming%20Guide_EN.pdf
"""

from heimdallr.instrument_control.categories.all_ctgs import *

class RigolDS1000Z(OscilloscopeCtg2):

	def __init__(self, address:str, log:plf.LogPile, **kwargs):
		super().__init__(address, log, expected_idn='RIGOL TECHNOLOGIES,DS10', **kwargs)
		
		self.meas_table = {OscilloscopeCtg2.MEAS_VMAX:'VMAX', OscilloscopeCtg2.MEAS_VMIN:'VMIN', OscilloscopeCtg2.MEAS_VAVG:'VAVG', OscilloscopeCtg2.MEAS_VPP:'VPP', OscilloscopeCtg2.MEAS_FREQ:'FREQ'}
		
		self.stat_table = {OscilloscopeCtg2.STAT_AVG:'AVER', OscilloscopeCtg2.STAT_MAX:'MAX', OscilloscopeCtg2.STAT_MIN:'MIN', OscilloscopeCtg2.STAT_CURR:'CURR', OscilloscopeCtg2.STAT_STD:'DEV'}
		
	def set_div_time(self, time_s:float):
		self.modify_state(self.get_div_time, OscilloscopeCtg1.DIV_TIME, time_s)
		self.write(f":TIM:MAIN:SCAL {time_s}")
	def get_div_time(self):
		return self.modify_state(None, OscilloscopeCtg1.DIV_TIME, self.query(f":TIM:MAIN:SCAL?"))
		
	
	def set_offset_time(self, time_s:float):
		self.modify_state(self.get_offset_time, OscilloscopeCtg1.OFFSET_TIME, time_s)
		self.write(f":TIM:MAIN:OFFS {time_s}")
	def get_offset_time(self):
		return self.modify_state(None, OscilloscopeCtg1.OFFSET_TIME, self.query(f":TIM:MAIN:OFFS?"))
	
	def set_div_volt(self, channel:int, volt_V:float):
		self.modify_state(self.get_div_volt, OscilloscopeCtg1.DIV_VOLT, volt_V, channel=channel)
		self.write(f":CHAN{channel}:SCAL {volt_V}")
	def get_div_volt(self, channel:int):
		return self.modify_state(None, OscilloscopeCtg1.DIV_VOLT, self.query(f":CHAN{channel}:SCAL?"), channel=channel)
	
	def set_offset_volt(self, channel:int, volt_V:float):
		self.modify_state(self.get_offset_volt, OscilloscopeCtg1.OFFSET_VOLT, volt_V, channel=channel)
		self.write(f":CHAN{channel}:OFFS {volt_V}")
	def get_offset_volt(self, channel:int):
		return self.modify_state(None, OscilloscopeCtg1.OFFSET_VOLT, self.query(f":CHAN{channel}:OFFS?"), channel=channel)
	
	def set_chan_enable(self, channel:int, enable:bool):
		self.modify_state(self.get_chan_enable, OscilloscopeCtg1.CHAN_EN, enable, channel=channel)
		self.write(f":CHAN{channel}:DISP {bool_to_str01(enable)}")
	def get_chan_enable(self, channel:int):
		val_str = self.query(f":CHAN{channel}:DISP?")
		return self.modify_state(None, OscilloscopeCtg1.CHAN_EN, str01_to_bool(val_str), channel=channel)
	
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
		
		self.modify_state(None, OscilloscopeCtg1.WAVEFORM, {"time_s":t, "volt_V":volts}, channel=channel)
		
		return {"time_s":t, "volt_V":volts}
	
	def add_measurement(self, meas_type:int, channel:int=1):
		
		# Find measurement string
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
		part of the OscilloscopeCtg1, but local to this driver.
		
		Args:
			enable (bool): Turns displayed stats on/off
		
		Returns:
			None
		'''
		
		self.write(f":MEASure:STATistic:DISPlay {bool_to_ONFOFF(enable)}")