from heimdallr.base import *

class DigitalMultimeterCtg(Driver):
	
	# TODO: Flesh out
	MEAS_RESISTANCE_2WIRE = "resistance-2wire"
	MEAS_RESISTANCE_4WIRE = "resistance-4wire"
	MEAS_VOLT_AC = "voltage-ac"
	MEAS_VOLT_DC = "voltage-dc"
	
	# TODO: Flesh out
	RANGE_AUTO = "auto-range"
	
	LOWPWR_MODE = "low-pow-mode[bool]"
	SEL_MEAS = "selected-meas[str]"
	LAST_MEAS_DATA = "last-meas-value[num]"
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn=""):
		super().__init__(address, log, expected_idn=expected_idn)
		
		self.state[DigitalMultimeterCtg.LOWPWR_MODE] = None
		self.state[DigitalMultimeterCtg.SEL_MEAS] = None
		self.state[DigitalMultimeterCtg.LAST_MEAS_DATA] = None
		
	@abstractmethod
	def set_low_power_mode(self, enable:bool, four_wire:bool=False):
		''' If enable is true, sets the resistance measurement to be in low-power mode.'''
		pass
	
	@abstractmethod
	def get_low_power_mode(self, four_wire:bool=False):
		''' Checks if low power mode is activated. '''
		pass
	
	@abstractmethod
	def set_measurement(self, measurement:str, meas_range:str=DigitalMultimeterCtg.RANGE_AUTO):
		''' Sets the measurement, using a DitigalMultimeterCtg constant. 
		Returns True if successful, else false.
		'''
		pass
	
	@abstractmethod
	def send_manual_trigger(self, send_cls:bool=True):
		''' Tells the instrument to begin measuring the selected parameter.'''
		pass
	
	@abstractmethod
	def get_last_value(self) -> float:
		''' Returns the last measured value. Will be in units self.check_units. Will return None on error '''
		pass
	
	@abstractmethod
	def send_trigger_and_read(self):
		''' Tells the instrument to read and returns teh measurement result. '''
		
		self.send_manual_trigger(send_cls=True)
		self.wait_ready()
		return self.get_last_value()