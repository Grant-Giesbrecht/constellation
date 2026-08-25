from constellation.base import *

import stardust.algorithm as stal

class PowerSupplyChannelState(InstrumentState):
	
	__state_fields__ = ("voltage_set", "current_set", "voltage_meas", "current_meas", "enable")
	
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

class PowerSupply(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn="", max_channels:int=1, dummy:bool=False, first_channel:int=1, **kwargs):
		_state = PowerSupplyState(first_channel, max_channels, log=log)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy, first_channel_num=first_channel, **kwargs)
		
		self.max_channels = max_channels
		
		if self.dummy:
			self.init_dummy_state()
		
	def init_dummy_state(self) -> None:
		
		
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.set_voltage(ch, 1)
			self.set_current(ch, 0.5)
			self.set_output_enable(ch, False)
	
	def remake_dummy_measurements(self):
		
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.state.channels[ch].voltage_meas = self.state.channels[ch].voltage_set + stal.randrange(-0.15, 0.15)
			self.state.channels[ch].current_meas = self.state.channels[ch].current_set + stal.randrange(-0.05, 0.05)
		
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Supplies SYNTHETIC dummy values only - see Oscilloscope.dummy_responder. Plain
		set_*/get_* methods are handled generically by modify_state() and need no case here.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			match func_name:
				case "get_measured_output":
					# Measured V/I are readings, not settings - invent them (with noise) from
					# the configured setpoints.
					self.remake_dummy_measurements()
					rval = (self.state.channels[args[0]].voltage_meas, self.state.channels[args[0]].current_meas)
				case _:
					return super().dummy_responder(func_name, *args, **kwargs)
			
			self.debug(f"Dummy responder sending >{protect_str(rval)}< to synthetic function (>{func_name}<).")
			return rval
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	@abstractmethod
	def set_voltage(self, channel:int, voltage:float):
		self.modify_state(lambda: self.get_voltage(channel), ["channels", "voltage_set"], voltage, indices=[channel])
	
	@abstractmethod
	def get_voltage(self, channel:int):
		return self.modify_state(None, ["channels", "voltage_set"], self._super_hint, indices=[channel])
		
	@abstractmethod
	def set_current(self, channel:int, current:float):
		self.modify_state(lambda: self.get_current(channel), ["channels", "current_set"], current, indices=[channel])
	
	@abstractmethod
	def get_current(self, channel:int):
		return self.modify_state(None, ["channels", "current_set"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_output_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_output_enable(channel), ["channels", "enable"], enable, indices=[channel])
	
	@abstractmethod
	def get_output_enable(self, channel:int):
		return self.modify_state(None, ["channels", "enable"], self._super_hint, indices=[channel])
	
	@abstractmethod
	@enabledummy
	def get_measured_output(self, channel:int):
		
		try:
			v_meas = self._super_hint[0]
			i_meas = self._super_hint[1]
		except Exception as e:
			self.error(f"Failed to unpack data in get_measured_output. ({e})")
			return (None, None)
			
		self.modify_state(None, ["channels", "voltage_meas"], v_meas, indices=[channel])
		self.modify_state(None, ["channels", "current_meas"], i_meas, indices=[channel])
		
		return (v_meas, i_meas)
	
	def refresh_state(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_voltage(ch)
			self.get_current(ch)
			self.get_output_enable(ch)
			self.get_measured_output(ch)
	
	def apply_state(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			
			chan = self.state.channels[ch]
			
			# Skip channels that have nothing stored yet. This used to be a blanket
			# try/except logging at lowdebug, which silently swallowed a call to a
			# nonexistent method (set_enable_output) for every channel - the state was never
			# applied and nothing said so. Check for unpopulated values explicitly instead,
			# so genuine failures below are visible.
			if chan is None or chan.voltage_set is None:
				self.lowdebug(f"Skipping apply_state for channel >{ch}<, not yet populated.")
				continue
			
			try:
				self.set_voltage(ch, chan.voltage_set)
				self.set_current(ch, chan.current_set)
				self.set_output_enable(ch, chan.enable)
			except Exception as e:
				self.error(f"Failed to apply state to channel >{ch}<. ({e})")
	
	def refresh_data(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_measured_output(ch)