"""RIGOL’s DP832 Power Supply
"""

from constellation.instrument_control.power_supply.power_supply_ctg import *

class RigolDP832(PowerSupplyCtg):

	def __init__(self, address:str, log:plf.LogPile, **kwargs):
		super().__init__(address, log, relay=DirectSCPIRelay(), expected_idn='RIGOL TECHNOLOGIES,DP832', max_channels=3, first_channel=1, **kwargs)
		
	@superreturn
	def set_voltage(self, channel:int, voltage:float):
		self.write(f":SOUR{channel}:VOLT {voltage}")
	
	@superreturn
	def get_voltage(self, channel:int):
		self._super_hint = float(self.query(f":SOUR{channel}:VOLT?").strip())
		
	@superreturn
	def set_current(self, channel:int, current:float):
		self.write(f":SOUR{channel}:CURR {current}") #TODO: Cannot except scientific notation
	
	@superreturn
	def get_current(self, channel:int):
		self._super_hint = float(self.query(f":SOUR{channel}:CURR?").strip())
	
	@superreturn
	def set_enable_output(self, channel:int, enable:bool):
		self.write(f":OUTP CH{channel},{bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_enable_output(self, channel:int):
		self._super_hint = str_to_bool(self.query(f":OUTP? CH{channel}"))
	
	@superreturn
	def get_output_measurement(self, channel:int):
		v_meas = float(self.query(f":MEAS:VOLT? CH{channel}").strip())
		i_meas = float(self.query(f":MEAS:CURR? CH{channel}").strip())
		self._super_hint = (v_meas, i_meas)
		