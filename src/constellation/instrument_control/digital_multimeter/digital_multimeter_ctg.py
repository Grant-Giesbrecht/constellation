from constellation.base import *
import stardust.algorithm as stal

class DigitalMultimeterState(InstrumentState):
	
	__state_fields__ = ("measurement_type", "trigger_type", "result_V", "result_I", "result_R")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("measurement_type", unit="CONST")
		self.add_param("trigger_type", unit="CONST")
		
		self.add_param("result_V", unit="V")
		self.add_param("result_I", unit="A")
		self.add_param("result_R", unit="Ohm")

class DigitalMultimeter(Driver):
	
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
		_state = DigitalMultimeterState(log=log)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy, **kwargs)
		
		if self.dummy:
			self.init_dummy_state()
		
	# Nominal readings dummy mode invents, per measurement function. A DMM measures whatever is
	# wired to it, so unlike a power supply there is no setpoint to derive a reading from - these
	# are simply plausible values for a bench measurement, with noise added per reading.
	#
	# `dummy_nominal` is a plain instance attribute, so a test or demo that needs a specific
	# reading can set it: `dmm.dummy_nominal[DigitalMultimeter.MEAS_VOLT_DC] = 5.0`.
	DUMMY_NOMINAL = {
		MEAS_VOLT_DC: (1.5, 0.002),          # (nominal, noise amplitude)
		MEAS_VOLT_AC: (0.707, 0.002),
		MEAS_CURR_DC: (0.025, 0.0001),
		MEAS_CURR_AC: (0.010, 0.0001),
		MEAS_RESISTANCE_2WIRE: (99.8, 0.05),
		MEAS_RESISTANCE_4WIRE: (99.8, 0.01),  # 4-wire removes lead resistance - less scatter
	}
	
	def init_dummy_state(self) -> None:
		self.dummy_nominal = dict(DigitalMultimeter.DUMMY_NOMINAL)
		
		self.set_measurement(DigitalMultimeter.MEAS_VOLT_DC)
		self.set_trigger_type(DigitalMultimeter.TRIG_CONT)
	
	def remake_dummy_reading(self) -> float:
		''' Invents a reading for the currently selected measurement function, and stores it in
		the matching result field.
		
		A meter reading is measurement data, not a setting - there is nothing in state to read
		back until something puts it there. Without this, a dummy DMM's get_value() returned
		None forever, because result_V/result_I/result_R start as None and nothing ever writes
		them.
		
		Returns:
			float: The generated reading, or None if the measurement type isn't recognized.
		'''
		
		meas = self.state.get(["measurement_type"])
		nominal_table = getattr(self, "dummy_nominal", DigitalMultimeter.DUMMY_NOMINAL)
		
		if meas not in nominal_table:
			self.error(f"Cannot generate a dummy reading for measurement type >{meas}<.")
			return None
		
		nominal, noise = nominal_table[meas]
		value = nominal + stal.randrange(-noise, noise)
		
		if meas in (DigitalMultimeter.MEAS_CURR_AC, DigitalMultimeter.MEAS_CURR_DC):
			self.state.result_I = value
		elif meas in (DigitalMultimeter.MEAS_VOLT_AC, DigitalMultimeter.MEAS_VOLT_DC):
			self.state.result_V = value
		else:
			self.state.result_R = value
		
		return value
	
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Supplies SYNTHETIC dummy values only - see Oscilloscope.dummy_responder. Plain
		set_*/get_* methods are handled generically by modify_state() and need no case here.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			match func_name:
				case "get_value":
					rval = self.remake_dummy_reading()
				case _:
					return super().dummy_responder(func_name, *args, **kwargs)
			
			self.debug(f"Dummy responder sending >{protect_str(rval)}< to synthetic function (>{func_name}<).")
			return rval
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	
	@abstractmethod
	def set_measurement(self, measurement:str, range:float=None):
		self.modify_state(self.get_measurement, ["measurement_type"], measurement)
	
	@abstractmethod
	def get_measurement(self):
		return self.modify_state(None, ["measurement_type"], self._super_hint)
	
	@abstractmethod
	def set_trigger_type(self, trig:str):
		self.modify_state(self.get_measurement, ["trigger_type"], trig)
	
	@abstractmethod
	def get_trigger_type(self):
		return self.modify_state(None, ["trigger_type"], self._super_hint)
	
	@abstractmethod
	def send_manual_trigger(self, send_cls:bool=True):
		''' Tells the instrument to begin measuring the selected parameter.'''
		pass
	
	@abstractmethod
	@enabledummy
	def get_value(self, check_measurement:bool=True):
		''' Queries the instrument and returns the last measured value.
		
		Carries @enabledummy because a meter reading is measurement data, not a setting: there is
		nothing tracked in state to read back until something invents one. See
		remake_dummy_reading().
		'''
		
		# Save super hint, it will be overridden by get_measurement()
		local_super_hint = self._super_hint
		
		if check_measurement:
			self.get_measurement()
		
		# Check if last value was a current
		if self.state.measurement_type in (DigitalMultimeter.MEAS_CURR_AC, DigitalMultimeter.MEAS_CURR_DC):
			return self.modify_state(None, ["result_I"], local_super_hint)
		elif self.state.measurement_type in (DigitalMultimeter.MEAS_VOLT_AC, DigitalMultimeter.MEAS_VOLT_DC):
			return self.modify_state(None, ["result_V"], local_super_hint)
		elif self.state.measurement_type in (DigitalMultimeter.MEAS_RESISTANCE_2WIRE, DigitalMultimeter.MEAS_RESISTANCE_4WIRE):
			return self.modify_state(None, ["result_R"], local_super_hint)
		else:
			self.error(f"Invalid measurement type >{self.state.measurement_type}<.")
			return None
	
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