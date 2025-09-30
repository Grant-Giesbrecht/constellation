""" NOte: This driver demonstrates how to use a non-default Relay, but it does not 
actually work. I think the LeCroy is using unconventional commands.
"""

from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *

class LeCroy44Xi(Oscilloscope):

	def __init__(self, address:str, log:plf.LogPile, dummy:bool=False, **kwargs):
		super().__init__(address, log, expected_idn='RIGOL TECHNOLOGIES,DS10', max_channels=4, num_div_horiz=12, dummy=dummy, num_div_vert=8, relay=VICPDirectSCPIRelay(), **kwargs)
		
		self.meas_table = {StdOscilloscopeCtg.MEAS_VMAX:'VMAX', StdOscilloscopeCtg.MEAS_VMIN:'VMIN', StdOscilloscopeCtg.MEAS_VAVG:'VAVG', StdOscilloscopeCtg.MEAS_VPP:'VPP', StdOscilloscopeCtg.MEAS_FREQ:'FREQ'}
		
		self.stat_table = {StdOscilloscopeCtg.STAT_AVG:'AVER', StdOscilloscopeCtg.STAT_MAX:'MAX', StdOscilloscopeCtg.STAT_MIN:'MIN', StdOscilloscopeCtg.STAT_CURR:'CURR', StdOscilloscopeCtg.STAT_STD:'DEV'}
	
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
		self._super_hint = self.query(f":CHAN{channel}:DISP?")
	
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
		
		self._super_hint = {"time_s":t, "volt_V":volts}
	
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