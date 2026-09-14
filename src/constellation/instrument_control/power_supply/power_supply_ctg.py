from constellation.base import *

import stardust.algorithm as stal

class PowerSupplyChannelState(InstrumentState):

	__state_fields__ = ("voltage_set", "current_set", "enable", "voltage_meas", "current_meas", "power_meas",
		"ovp_level", "ovp_enable", "ovp_tripped", "ocp_level", "ocp_enable", "ocp_tripped")

	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)

		self.add_param("voltage_set", unit="V")
		self.add_param("current_set", unit="A")
		self.add_param("enable", unit="bool")

		# Readings taken at the output terminals, not settings.
		self.add_param("voltage_meas", unit="V", is_data=True)
		self.add_param("current_meas", unit="A", is_data=True)
		self.add_param("power_meas", unit="W", is_data=True)

		self.add_param("ovp_level", unit="V")
		self.add_param("ovp_enable", unit="bool")
		self.add_param("ovp_tripped", unit="bool")

		self.add_param("ocp_level", unit="A")
		self.add_param("ocp_enable", unit="bool")
		self.add_param("ocp_tripped", unit="bool")

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
			self.set_ovp_level(ch, 5.0)
			self.set_ovp_enable(ch, False)
			self.set_ocp_level(ch, 1.0)
			self.set_ocp_enable(ch, False)
			self.clear_ovp_trip(ch)
			self.clear_ocp_trip(ch)
			self.set_output_enable(ch, False)

	def _dummy_output(self, channel:int) -> tuple:
		''' Synthetic (voltage, current) at a channel's terminals: the setpoints plus noise while the
		output is on, and roughly zero while it is off. '''

		chan = self.state.channels[channel]

		if not chan.enable:
			return (stal.randrange(-0.002, 0.002), stal.randrange(-0.0005, 0.0005))

		return ((chan.voltage_set or 0.0) + stal.randrange(-0.15, 0.15), (chan.current_set or 0.0) + stal.randrange(-0.05, 0.05))

	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Supplies SYNTHETIC dummy values only - see Oscilloscope.dummy_responder. Plain
		set_*/get_* methods are handled generically by modify_state() and need no case here.
		'''

		# Put everything in a try-catch in case arguments are missing or similar
		try:

			match func_name:
				# Readings are not settings - invent them from the configured setpoints. Each is
				# recorded in state here, since the category method that would record it is skipped.
				case "get_measured_voltage":
					rval = self._dummy_output(args[0])[0]
					self.state.set(["channels", "voltage_meas"], rval, indices=[args[0]])
				case "get_measured_current":
					rval = self._dummy_output(args[0])[1]
					self.state.set(["channels", "current_meas"], rval, indices=[args[0]])
				case "get_measured_power":
					voltage, current = self._dummy_output(args[0])
					rval = voltage * current
					self.state.set(["channels", "power_meas"], rval, indices=[args[0]])
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
	def get_measured_voltage(self, channel:int):
		''' Voltage measured at the channel's output terminals. '''
		return self.modify_state(None, ["channels", "voltage_meas"], self._super_hint, indices=[channel])

	@abstractmethod
	@enabledummy
	def get_measured_current(self, channel:int):
		''' Current measured at the channel's output terminals. '''
		return self.modify_state(None, ["channels", "current_meas"], self._super_hint, indices=[channel])

	@abstractmethod
	@enabledummy
	def get_measured_power(self, channel:int):
		''' Power measured at the channel's output terminals. '''
		return self.modify_state(None, ["channels", "power_meas"], self._super_hint, indices=[channel])

	@abstractmethod
	def set_ovp_level(self, channel:int, voltage:float):
		''' Over-voltage protection threshold: with protection enabled, the output turns off if its
		voltage exceeds this. '''
		self.modify_state(lambda: self.get_ovp_level(channel), ["channels", "ovp_level"], voltage, indices=[channel])

	@abstractmethod
	def get_ovp_level(self, channel:int):
		return self.modify_state(None, ["channels", "ovp_level"], self._super_hint, indices=[channel])

	@abstractmethod
	def set_ovp_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_ovp_enable(channel), ["channels", "ovp_enable"], enable, indices=[channel])

	@abstractmethod
	def get_ovp_enable(self, channel:int):
		return self.modify_state(None, ["channels", "ovp_enable"], self._super_hint, indices=[channel])

	@abstractmethod
	def get_ovp_tripped(self, channel:int):
		''' Whether over-voltage protection has tripped. Stays set until cleared. '''
		return self.modify_state(None, ["channels", "ovp_tripped"], self._super_hint, indices=[channel])

	@abstractmethod
	def clear_ovp_trip(self, channel:int):
		''' Clears a tripped over-voltage protection. Remove the cause first, or it trips again. '''
		self.modify_state(lambda: self.get_ovp_tripped(channel), ["channels", "ovp_tripped"], False, indices=[channel])

	@abstractmethod
	def set_ocp_level(self, channel:int, current:float):
		''' Over-current protection threshold: with protection enabled, the output turns off if its
		current exceeds this. '''
		self.modify_state(lambda: self.get_ocp_level(channel), ["channels", "ocp_level"], current, indices=[channel])

	@abstractmethod
	def get_ocp_level(self, channel:int):
		return self.modify_state(None, ["channels", "ocp_level"], self._super_hint, indices=[channel])

	@abstractmethod
	def set_ocp_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_ocp_enable(channel), ["channels", "ocp_enable"], enable, indices=[channel])

	@abstractmethod
	def get_ocp_enable(self, channel:int):
		return self.modify_state(None, ["channels", "ocp_enable"], self._super_hint, indices=[channel])

	@abstractmethod
	def get_ocp_tripped(self, channel:int):
		''' Whether over-current protection has tripped. Stays set until cleared. '''
		return self.modify_state(None, ["channels", "ocp_tripped"], self._super_hint, indices=[channel])

	@abstractmethod
	def clear_ocp_trip(self, channel:int):
		''' Clears a tripped over-current protection. Remove the cause first, or it trips again. '''
		self.modify_state(lambda: self.get_ocp_tripped(channel), ["channels", "ocp_tripped"], False, indices=[channel])

	def refresh_state(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_voltage(ch)
			self.get_current(ch)
			self.get_output_enable(ch)
			self.get_ovp_level(ch)
			self.get_ovp_enable(ch)
			self.get_ovp_tripped(ch)
			self.get_ocp_level(ch)
			self.get_ocp_enable(ch)
			self.get_ocp_tripped(ch)
			self.get_measured_voltage(ch)
			self.get_measured_current(ch)
			self.get_measured_power(ch)

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

			# Snapshot first: on hardware each setter reads its value back into this same state.
			settings = {name: getattr(chan, name) for name in ("voltage_set", "current_set", "ovp_level", "ovp_enable", "ocp_level", "ocp_enable", "enable")}

			try:
				self.set_voltage(ch, settings["voltage_set"])
				self.set_current(ch, settings["current_set"])

				# Protection before the output, so an output switched on is already protected.
				for name, setter in (("ovp_level", self.set_ovp_level), ("ovp_enable", self.set_ovp_enable),
						("ocp_level", self.set_ocp_level), ("ocp_enable", self.set_ocp_enable)):
					if settings[name] is not None:
						setter(ch, settings[name])

				if settings["enable"] is not None:
					self.set_output_enable(ch, settings["enable"])
			except Exception as e:
				self.error(f"Failed to apply state to channel >{ch}<. ({e})")

	def refresh_data(self):
		for ch in range(self.first_channel, self.first_channel+self.max_channels):
			self.get_measured_voltage(ch)
			self.get_measured_current(ch)
			self.get_measured_power(ch)
