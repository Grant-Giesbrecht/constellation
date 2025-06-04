""" Rohde & Schwarz SGMA RF Signal Generator
"""

from heimdallr.base import *
from heimdallr.instrument_control.categories.rf_signal_generator_ctg import *

class RohdeSchwarz_SGMA(RFSignalGeneratorCtg):

	def __init__(self, address:str, log:LogPile):
		# Example: "HEWLETT-PACKARD,83650L,3844A00476,19 JAN 00\n"
		super().__init__(address, log, expected_idn="Rohde&Schwarz,SGS100")	
		
	
	def set_power(self, p_dBm:float):
		self.write(f":POW:LEV {p_dBm}")
	def get_power(self):
		val = self.query(f":POW:LEV?")
		return float(val)
	
	def set_freq(self, f_Hz:float):
		self.inst.write(f":SOUR:FREQ:CW {f_Hz}")
	def get_freq(self):
		return float(self.inst.query(f":SOUR:FREQ:CW?"))
	
	def set_enable_rf(self, enable:bool):
		self.inst.write(f":OUTP:STAT {bool_to_str01(enable)}")
	def get_enable_rf(self):
		return str_to_bool(self.inst.query(f":OUTP:STAT?"))