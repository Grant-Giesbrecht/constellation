from heimdallr.base import *

class DigitalMultimeterCtg0(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn=""):
		super().__init__(address, log, expected_idn=expected_idn)

class DigitalMultimeterCtg1(DigitalMultimeterCtg0):
	
	# TODO: Flesh out
	MEAS_RESISTANCE_2WIRE = "resistance-2wire"
	MEAS_RESISTANCE_4WIRE = "resistance-4wire"
	MEAS_VOLT_AC = "voltage-ac"
	MEAS_VOLT_DC = "voltage-dc"
	
	# TODO: Flesh out
	RANGE_AUTO = "auto-range"
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn=""):
		super().__init__(address, log, expected_idn=expected_idn)
	