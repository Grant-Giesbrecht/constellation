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

class SiglentSDM3000X(BasicDigitalMultimeterCtg):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, expected_idn="Siglent Technologies,SDM30") 
		
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
				mstr = f"RES:DC {range_str}"
				self.check_units = "OHM"
			case BasicDigitalMultimeterCtg.MEAS_RESISTANCE_4WIRE:
				mstr = f"FRES:DC {range_str}"
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
		
		self.write(f"CONFigure:{mstr}")

	
	def send_manual_trigger(self, send_cls:bool=True):
		''' Tells the instrument to begin measuring the selected parameter.'''
		
		if send_cls:
			self.write("*CLS")
		self.write(f"INIT:IMM")
	
	def get_last_value(self) -> float:
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
			self.log.error(f"Failed to convert string data to gloat.", detail=f"({e})")
			return None
		
		# Check units
		try:
			unit_str = str_val[last_space+1:last_space+1+len(self.check_units)]
			if self.check_units != unit_str:
				self.log.error(f"Received wrong type of units. Aborting.", detail=f"Received '{unit_str}', expected '{self.check_units}'.")
				return None
		except Exception as e:
			try:
				unit_str = str_val[last_space+1:]
			except:
				unit_str = "??"
			
			self.log.error(f"Received wrong type of units. Aborting.", detail=f"Received '{unit_str}', expected '{self.check_units}' ({e}).")
			return None
		
		self.modify_state(None, DigitalMultimeterCtg.LAST_MEAS_DATA, val)
		
		return val
