""" Keysight 8360L Series Swept CW Generator
"""

from heimdallr.base import *
from heimdallr.instrument_control.categories.rf_signal_generator_ctg import *

class Keysight8360L(RFSignalGeneratorCtg1):

	def __init__(self, address:str, log:plf.LogPile):
		# Example: "HEWLETT-PACKARD,83650L,3844A00476,19 JAN 00\n"
		super().__init__(address, log, expected_idn="HEWLETT-PACKARD,836")
		
	
	def set_power(self, p_dBm:float):
		self.modify_state(self.get_power, RFSignalGeneratorCtg1.POWER, p_dBm)
		self.write(f":POW:LEV {p_dBm}")
	def get_power(self):
		val = self.query(f":POW:LEV?")
		return self.modify_state(None, RFSignalGeneratorCtg1.POWER, float(val))
	
	def set_freq(self, f_Hz:float):
		self.modify_state(self.get_freq, RFSignalGeneratorCtg1.FREQ, f_Hz)
		self.inst.write(f":SOUR:FREQ:CW {f_Hz}")
	def get_freq(self):
		return self.modify_state(None, RFSignalGeneratorCtg1.FREQ, float(self.inst.query(f":SOUR:FREQ:CW?")))
	
	def set_enable_rf(self, enable:bool):
		self.modify_state(self.get_enable_rf, RFSignalGeneratorCtg1.ENABLE, enable)
		self.inst.write(f":OUTP:STAT {bool_to_str01(enable)}")
	def get_enable_rf(self):
		return self.modify_state(None, RFSignalGeneratorCtg1.ENABLE, str_to_bool(self.inst.query(f":OUTP:STAT?")))