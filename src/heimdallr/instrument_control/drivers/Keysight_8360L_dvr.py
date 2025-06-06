""" Keysight 8360L Series Swept CW Generator
"""

from heimdallr.base import *
from heimdallr.instrument_control.categories.rf_signal_generator_ctg import *

class Keysight8360L(RFSignalGeneratorCtg):

	def __init__(self, address:str, log:plf.LogPile):
		# Example: "HEWLETT-PACKARD,83650L,3844A00476,19 JAN 00\n"
		super().__init__(address, log, expected_idn="HEWLETT-PACKARD,836")
		
	
	def set_power(self, p_dBm:float):
		self.write(f":POW:LEV {p_dBm}")
		self.modify_state(self.get_power, RFSignalGeneratorCtg.POWER, p_dBm)
	def get_power(self):
		val = self.query(f":POW:LEV?")
		return self.modify_state(None, RFSignalGeneratorCtg.POWER, float(val))
	
	def set_freq(self, f_Hz:float):
		self.inst.write(f":SOUR:FREQ:CW {f_Hz}")
		self.modify_state(self.get_freq, RFSignalGeneratorCtg.FREQ, f_Hz)
	def get_freq(self):
		return self.modify_state(None, RFSignalGeneratorCtg.FREQ, float(self.query(f":SOUR:FREQ:CW?")))
	
	def set_enable_rf(self, enable:bool):
		self.inst.write(f":OUTP:STAT {bool_to_str01(enable)}")
		self.modify_state(self.get_enable_rf, RFSignalGeneratorCtg.ENABLE, enable)
	def get_enable_rf(self):
		return self.modify_state(None, RFSignalGeneratorCtg.ENABLE, str_to_bool(self.query(f":OUTP:STAT?")))