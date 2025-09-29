from constellation.base import *
from constellation.networking.net_client import *

class PowerSupplyChannelState(InstrumentState):
	
	__state_fields__ = ("voltage_set", "current_set", "voltage_meas", "current_meas", "enabled")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("voltage_set", unit="V")
		self.add_param("current_set", unit="A")
		self.add_param("voltage_meas", unit="V")
		self.add_param("current_meas", unit="A")
		
		self.add_param("enable", unit="bool")

class PowerSupplyState(InstrumentState):
	
	__state_fields__ = ("first_channel", "num_channels", "channels")
	
	def __init__(self, first_channel:int, num_channels:int, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		
		self.add_param("channels", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=PowerSupplyChannelState, log=log))
		
		for ch_no in self.channels.get_range():
			self.channels[ch_no] = PowerSupplyChannelState(log=log)

class PowerSupplyCtg(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn="", max_channels:int=1, dummy:bool=False, first_channel:int=1, **kwargs):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, relay=relay, **kwargs)
		
		self.first_channel = first_channel
		self.max_channels = max_channels
		
		self.state = PowerSupplyState(self.first_channel, self.max_channels, log=log)
		
		if self.dummy:
			self.init_dummy_state()
		
	def init_dummy_state(self) -> None:
		pass
	
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Function expected to behave as the "real" equivalents. ie. write commands don't
		need to return anything, reads commands or similar should. What is returned here
		should mimic what would be returned by the "real" function if it were connected to
		hardware.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			# Check for known functions
			found = True
			adjective = ""
			match func_name:
				case "set_voltage":
					rval = None
				case "get_voltage":
					rval = self.state.channels[args[0]].voltage_set
				case "set_current":
					rval = None
				case "get_current":
					rval = self.state.channels[args[0]].current_set
				case "set_enable":
					rval = None
				case "get_enable":
					rval = self.state.channels[args[0]].enable
				case "get_output_measurement":
					rval = (self.state.channels[args[0]].voltage_meas, self.state.channels[args[0]].current_meas)
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
	def set_voltage(self, channel:int, voltage:float):
		self.modify_state(lambda: self.get_voltage(channel), ["channels", "voltage_set"], voltage, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_voltage(self, channel:int):
		return self.modify_state(None, ["channels", "voltage_set"], self._super_hint, indices=[channel])
		
	@abstractmethod
	def set_current(self, channel:int, current:float):
		self.modify_state(lambda: self.get_voltage(channel), ["channels", "current_set"], current, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_current(self, channel:int):
		return self.modify_state(None, ["channels", "current_set"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_enable_output(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_voltage(channel), ["channels", "enable"], enable, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_enable_output(self, channel:int):
		return self.modify_state(None, ["channels", "enable"], self._super_hint, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_output_measurement(self, channel:int):
		
		try:
			v_meas = self._super_hint[0]
			i_meas = self._super_hint[1]
		except Exception as e:
			self.error(f"Failed to unpack data in get_output_measurement. ({e})")
			return (None, None)
			
		self.modify_state(None, ["channels", "voltage_meas"], v_meas, indices=[channel])
		self.modify_state(None, ["channels", "current_meas"], i_meas, indices=[channel])
		
		return (v_meas, i_meas)
	
	def refresh_state(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_voltage(ch)
			self.get_current(ch)
			self.get_enable_output(ch)
			self.get_output_measurement(ch)
	
	def apply_state(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			try:
				self.set_voltage(ch, self.state.channels[ch].voltage_set)
				self.set_current(ch, self.state.channels[ch].current_set)
				self.set_enable_output(ch, self.state.channels[ch].enable)
			except Exception as e:
				self.lowdebug(f"Skipping apply state for channels not yet populated. ({e})")
	
	def refresh_data(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_output_measurement(ch)