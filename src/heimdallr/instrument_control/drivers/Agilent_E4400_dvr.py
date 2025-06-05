""" Keysight 8360L Series Swept CW Generator

Manual: http://www.doe.carleton.ca/~nagui/labequip/synth/manuals/e4400324.pdf
"""

from heimdallr.base import *
from heimdallr.instrument_control.categories.rf_signal_generator_ctg import *

class AgilentE4400(RFSignalGeneratorCtg1):

	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, expected_idn='Hewlett-Packard, ESG-4000B')
	
	def set_power(self, p_dBm:float):
		self.modify_state(self.get_power, RFSignalGeneratorCtg1.POWER, p_dBm)
		self.write(f":POW:LEV:IMM:AMPL {p_dBm} dBm")
	def get_power(self):
		val = self.query(f":POW:LEV:IMM:AMPL?")
		return self.modify_state(None, RFSignalGeneratorCtg1.POWER, float(val))
	
	def set_freq(self, f_Hz:float):
		self.modify_state(self.get_freq, RFSignalGeneratorCtg1.FREQ, f_Hz)
		self.write(f":FREQ:CW {f_Hz} Hz")
	def get_freq(self):
		return self.modify_state(None, RFSignalGeneratorCtg1.FREQ, float(self.query(f":FREQ:CW?")))
	
	def set_enable_rf(self, enable:bool):
		self.modify_state(self.get_enable_rf, RFSignalGeneratorCtg1.ENABLE, enable)
		self.write(f":OUTP:STAT {bool_to_str01(enable)}")
	def get_enable_rf(self):
		return self.modify_state(None, RFSignalGeneratorCtg1.ENABLE, str_to_bool(self.query(f":OUTP:STAT?")))