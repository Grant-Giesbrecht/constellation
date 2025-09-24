''' Driver for Siglent SDM3000X series digital multimeters:
 - SDM3045X
 - SDM3055X
 - SDM3065X
Only tested with SDM3045X.

https://int.siglent.com/u_file/document/SDM%20Series%20Digital%20Multimeter_ProgrammingGuide_EN02A.pdf
'''

import array
from constellation.base import *
from constellation.instrument_control.digital_multimeter.digital_multimeter_ctg import *

class Keysight34400(BasicDigitalMultimeterCtg):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, relay=DirectSCPIRelay(), expected_idn="Keysight Technologies,344") 
		
		# Unit to make sure is matched by returned string
		self.check_units = ""
	
	@superreturn
	def set_measurement(self, measurement:str, range:float=None):
		''' Sets the measurement, using a DitigalMultimeterCtg0 constant. 
		Returns True if successful, else false.
		'''
		
		#TODO: Validate ranges
		# SDM3045X: {600uA|6mA|60mA|600mA|6A|10A|AUTO}, AC starts at 60 mA
		# SDM3055 and 3065X: {200uA|2mA|20mA|200mA|2A|10A|AUTO}, AC starts at 
		if range is None:
			range_str = "AUTO"
		else:
			range_str = f"{range}"
		
		# Get measurement string
		match measurement:
			case BasicDigitalMultimeterCtg.MEAS_RESISTANCE_2WIRE:
				mstr = f"RES {range_str}"
				self.check_units = "OHM"
			case BasicDigitalMultimeterCtg.MEAS_RESISTANCE_4WIRE:
				mstr = f"FRES {range_str}"
				self.check_units = "OHM"
			case BasicDigitalMultimeterCtg.MEAS_CURR_AC:
				mstr = f"CURR:AC {range_str}"
				self.check_units = "A"
			case BasicDigitalMultimeterCtg.MEAS_CURR_DC:
				mstr = f"CURR:DC {range_str}"
				self.check_units = "A"
			case BasicDigitalMultimeterCtg.MEAS_VOLT_AC:
				mstr = f"VOLT:AC {range_str}"
				self.check_units = "V"
			case BasicDigitalMultimeterCtg.MEAS_VOLT_DC:
				mstr = f"VOLT:DC {range_str}"
				self.check_units = "V"
			case _:
				self.log.error(f"Failed to interpret measurement argument '{measurement}'. Aborting.")
				return False
		
		self.write(f"ABORT") # Abort any previous wait for trigger events
		self.write(f"CONFigure:{mstr}")
	
	def pop_error_queue(self) -> str:
		''' Queries the instrument for it's last error 
		'''
		#TODO: Force all instrument to provide?
		
		return self.query("SYSTEM:ERROR?")
	
	@superreturn
	def get_measurement(self):
		#TODO: Handle range somehow
		code = self.query(":FUNC?").strip().upper().replace('"', '')
		
		if code == "VOLT" or code =="VOLT:DC":
			self._super_hint = BasicDigitalMultimeterCtg.MEAS_VOLT_DC
		elif code == "VOLT:AC":
			self._super_hint = BasicDigitalMultimeterCtg.MEAS_VOLT_AC
		elif code == "CURR" or code == "CURR:DC":
			self._super_hint = BasicDigitalMultimeterCtg.MEAS_CURR_DC
		elif code == "CURR:AC":
			self._super_hint = BasicDigitalMultimeterCtg.MEAS_CURR_AC
		elif code == "RES":
			self._super_hint = BasicDigitalMultimeterCtg.MEAS_RESISTANCE_2WIRE
		elif code == "FRES":
			self._super_hint = BasicDigitalMultimeterCtg.MEAS_RESISTANCE_4WIRE
		else:
			self.error(f"Received unknown measurement type >{code}<.")
			self._super_hint = "?"
	
	@superreturn
	def set_trigger_type(self, trig: str):
		
		if trig == BasicDigitalMultimeterCtg.TRIG_CONT:
			self.write(f"ABORT") # Abort any previous wait for trigger events
			self.write(f"TRIG:SOUR IMM")
			self.write(f"TRIG:COUN INF") # No limit to num trig
			self.write(f"INIT:IMM")
		elif trig == BasicDigitalMultimeterCtg.TRIG_SINGLE:
			self.write(f"ABORT") # Abort any previous wait for trigger events
			self.write(f"TRIG:SOUR IMM")
			self.write(f"TRIG:COUN 1") # No limit to num trig
		elif trig == BasicDigitalMultimeterCtg.TRIG_EXT:
			self.write(f"ABORT") # Abort any previous wait for trigger events
			self.write(f"TRIG:SOUR EXT")
			self.write(f"TRIG:COUN 1") # No limit to num trig
			#TODO: Control rise or falling edge
		else:
			self.error(f"Received unrecognized trigger type code >{trig}<.")
	
	@superreturn
	def get_trigger_type(self):
		src = self.query(f"TRIG:SOUR?").strip().upper().replace('"', '')
		count = self.query(f"TRIG:COUN?").strip().upper().replace('"', '')
		
		if src == "IMM":
			try:
				count = float(count) # Will be 9.9e37 for INF
				if count == 1:
					self._super_hint = BasicDigitalMultimeterCtg.TRIG_SINGLE
				else:
					self._super_hint = BasicDigitalMultimeterCtg.TRIG_CONT
			except:
				self.error(f"Failed to get trigger type. Invalid count string >{count}<.")
				self._super_hint = "?"
		elif src == "EXT":
			self._super_hint = BasicDigitalMultimeterCtg.TRIG_EXT
		else:
			self.error(f"Failed to get trigger type. Invalid trigger type string >{src}<.")
			self._super_hint = "?"
	
	def send_manual_trigger(self, send_cls:bool=True):
		''' Tells the instrument to begin measuring the selected parameter.'''
		
		if send_cls:
			self.write("*CLS")
		self.write(f"INIT:IMM")
	
	@superreturn
	def get_value(self, check_measurement:bool=True) -> float:
		''' Returns the last measured value. Will be in units self.check_units. Will return None on error '''
		
		str_val = self.query("DATA:LAST?")
		
		# Remove line endings
		str_val = str_val.strip()
		
		# Remove units from string
		first_space = str_val.find(' ') # Find first space
		last_space = str_val.rfind(' ') # Find last space
		
		# Get flaot data
		try:
			val = float(str_val[:first_space])
		except Exception as e:
			self.log.error(f"Failed to convert string data to float.", detail=f"({e})")
			return None
		
		#TODO: Check for overload
		
		#TODO: Implement check units
		# # Check units
		# try:
		# 	unit_str = str_val[last_space+1:last_space+1+len(self.check_units)]
		# 	if self.check_units != unit_str:
		# 		self.log.error(f"Received wrong type of units. Aborting.", detail=f"Received '{unit_str}', expected '{self.check_units}'.")
		# 		return None
		# except Exception as e:
		# 	try:
		# 		unit_str = str_val[last_space+1:]
		# 	except:
		# 		unit_str = "??"
		# 	
		# 	self.log.error(f"Received wrong type of units. Aborting.", detail=f"Received '{unit_str}', expected '{self.check_units}' ({e}).")
		# 	return None
		
		self._super_hint = val