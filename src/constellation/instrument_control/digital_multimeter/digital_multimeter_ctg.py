from constellation.base import *
from constellation.networking.net_client import *

class BasicDigitalMultimeterState(InstrumentState):
	
	__state_fields__ = ("measurement_type", "trigger_type", "result_V", "result_I", "result_R")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("measurement_type", unit="CONST")
		self.add_param("trigger_type", unit="CONST")
		
		self.add_param("result_V", unit="V")
		self.add_param("result_I", unit="A")
		self.add_param("result_R", unit="Ohm")

class BasicDigitalMultimeterCtg(Driver):
	
	TRIG_CONT = "trig-continuous"
	TRIG_SINGLE = "trig-single"
	TRIG_EXT = "trig-external"
	
	MEAS_RESISTANCE_2WIRE = "resistance-2wire"
	MEAS_RESISTANCE_4WIRE = "resistance-4wire"
	MEAS_VOLT_AC = "voltage-ac"
	MEAS_VOLT_DC = "voltage-dc"
	MEAS_CURR_AC = "current-ac"
	MEAS_CURR_DC = "current-dc"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn="", dummy:bool=False, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, relay=relay, **kwargs)
		
		self.state = BasicDigitalMultimeterState(log=log)
		
		if self.dummy:
			self.init_dummy_state()
		
	def init_dummy_state(self) -> None:
		self.set_measurement(BasicDigitalMultimeterCtg.MEAS_VOLT_DC)
		self.set_trigger_type(BasicDigitalMultimeterCtg.TRIG_CONT)
	
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Function expected to behave as the "real" equivalents. ie. write commands don't
		need to return anything, reads commands or similar should. What is returned here
		should mimic what would be returned by the "real" function if it were connected to
		hardware.
		'''
		
		def return_selected(self):
			# Check if last value was a current
			if self.state.measurement_type in (BasicDigitalMultimeterCtg.MEAS_CURR_AC, BasicDigitalMultimeterCtg.MEAS_CURR_DC):
				return self.state.result_I
			elif self.state.measurement_type in (BasicDigitalMultimeterCtg.MEAS_VOLT_AC, BasicDigitalMultimeterCtg.MEAS_VOLT_DC):
				return self.state.result_V
			elif self.state.measurement_type in (BasicDigitalMultimeterCtg.MEAS_RESISTANCE_2WIRE, BasicDigitalMultimeterCtg.MEAS_RESISTANCE_4WIRE):
				return self.state.result_B
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			# Check for known functions
			found = True
			adjective = ""
			match func_name:
				case "set_measurement":
					rval = None
				case "get_measurement":
					rval = self.state.get(["measurement_type"])
				case "set_trigger_type":
					rval = None
				case "get_trigger_type":
					rval = self.state.get(["trigger_type"])
				case "get_value":
					rval = return_selected(self)
				case "send_trigger_and_read":
					rval = return_selected(self)
				case _:
					found = False
			
			# If function was found, label as recognized, else check match for general getter or setter
			if found:
				adjective = "recognized"
			else:
				if "set_" == func_name[:4]:
					rval = -1
					adjective = "set_"
				elif "get_" == func_name[:4]:
					rval = None
					adjective = "get_"
				else:
					rval = None
					adjective = "unrecognized"
				
			self.debug(f"Dummy responder sending >{protect_str(rval)}< to {adjective} function (>{func_name}<).")
			return rval
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	
	@abstractmethod
	def set_measurement(self, measurement:str):
		self.modify_state(self.get_measurement, ["measurement_type"], measurement)
	
	@abstractmethod
	@enabledummy
	def get_measurement(self):
		return self.modify_state(None, ["measurement_type"], self._super_hint)
	
	@abstractmethod
	def set_trigger_type(self, trig:str):
		self.modify_state(self.get_measurement, ["trigger_type"], trig)
	
	@abstractmethod
	@enabledummy
	def get_trigger_type(self):
		return self.modify_state(None, ["trigger_type"], self._super_hint)
	
	@abstractmethod
	def send_manual_trigger(self, send_cls:bool=True):
		''' Tells the instrument to begin measuring the selected parameter.'''
		pass
	
	@abstractmethod
	def get_value(self):
		''' Returns the last measured value.'''
		
		# Check if last value was a current
		if self.state.measurement_type in (BasicDigitalMultimeterCtg.MEAS_CURR_AC, BasicDigitalMultimeterCtg.MEAS_CURR_DC):
			return self.modify_state(None, ["result_I"], self._super_hint)
		elif self.state.measurement_type in (BasicDigitalMultimeterCtg.MEAS_VOLT_AC, BasicDigitalMultimeterCtg.MEAS_VOLT_DC):
			return self.modify_state(None, ["result_V"], self._super_hint)
		elif self.state.measurement_type in (BasicDigitalMultimeterCtg.MEAS_RESISTANCE_2WIRE, BasicDigitalMultimeterCtg.MEAS_RESISTANCE_4WIRE):
			return self.modify_state(None, ["result_R"], self._super_hint)
	
	def send_trigger_and_read(self):
		''' Tells the instrument to read and returns teh measurement result. '''
		
		self.send_manual_trigger(send_cls=True)
		self.wait_ready()
		return self.get_value()
	
	def refresh_state(self):
		self.get_measurement()
		self.get_trigger_type()
	
	def apply_state(self):		
		self.set_measurement(self.state.measurement_type)
		self.set_trigger_type(self.state.trigger_type)

	
	def refresh_data(self):
		self.get_value()