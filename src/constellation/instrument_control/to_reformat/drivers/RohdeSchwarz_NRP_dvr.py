''' Driver for Rohde & Schwarz NRX Power Meter 

Manual: https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/pdm/cl_manuals/user_manual/1178_5566_01/NRX_UserManual_en_10.pdf
	    https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/pdm/cl_manuals/user_manual/1177_5079_01/NRPxxSN_UserManual_en_21.pdf
'''

from constellation.base import *
from constellation.instrument_control.categories.rf_power_sensor_ctg import *

class RohdeSchwarzNRP(RFPowerSensor):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, expected_idn="Rohde&Schwarz,NRP") # Example string:  ''
		
	def set_meas_frequency(self, f_Hz:float):
		self.write(f"SENSE:FREQUENCY {f_Hz}")
		self.modify_state(self.get_meas_frequency, RFPowerSensor.FREQ, f_Hz)
	def get_meas_frequency(self) -> float:
		return self.modify_state(None, RFPowerSensor.FREQ, float(self.query(f"SENSE:FREQUENCY?")))
	
	# def send_autoscale(self):
	# 	print("Should this be send or enable? Is it one time?")
	# 	self.write(f":SENS:POW:RANGE:AUTO ON")
	
	def send_trigger(self, wait:bool=False):
		
		# Set unit to dBm - if done after trigger, seems to void results
		self.write("UNIT:POW DBM")
		
		self.write(f"*CLS") # Clear status register so can determine when ready
		
		self.write("INITIATE")
		
		# Wait for operation to complete if requested
		if wait:
			self.wait_ready()
		
	
	def get_measurement(self):
		
		#TODO: Verify it returns in dBm
		data = float(self.query(f"FETCH?"))
		return self.modify_state(None, RFPowerSensor.LAST_DATA, data)
	
	# def set_averaging_count(self, counts:int, meas_no:int=1):
		
	# 	 # Enforce bounds - counts
	# 	counts = max(1, min(counts, 1048576))
	# 	if counts != counts:
	# 		self.log.error(f"Did not apply command. Instrument limits number of counts from 1 to 1048576 and this range was violated.")
	# 		return
		
	# 	# Enforce bounds - meas_no
	# 	meas_no = max(1, min(meas_no, 8))
	# 	if meas_no != meas_no:
	# 		self.log.error(f"Did not apply command. Instrument limits measurement-number values from 1 to 8 and this range was violated.")
	# 		return
		
	# 	# Legacy version, works for R&S but deprecated
	# 	#  [SENSe<Sensor>:]AVERage:COUNt[:VALue]
		
	#	self.write(f"CALC{meas_no}:CHAN1:AVER:COUN:VAL {counts}")

