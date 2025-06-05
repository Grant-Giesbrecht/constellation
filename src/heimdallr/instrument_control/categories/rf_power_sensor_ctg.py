from heimdallr.base import *

class RFPowerSensor(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn=""):
		super().__init__(address, log, expected_idn=expected_idn)