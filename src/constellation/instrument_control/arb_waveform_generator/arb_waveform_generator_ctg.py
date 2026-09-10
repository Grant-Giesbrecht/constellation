from constellation.base import *

class AWGChannelState(InstrumentState):
	
	__state_fields__ = ("waveform_type", "frequency", "amplitude", "offset", "phase",
		"duty_cycle", "output_enable", "output_load", "output_polarity")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("waveform_type", unit="")
		self.add_param("frequency", unit="Hz")
		self.add_param("amplitude", unit="Vpp")
		self.add_param("offset", unit="V")
		self.add_param("phase", unit="deg")
		self.add_param("duty_cycle", unit="%")
		
		self.add_param("output_enable", unit="bool")
		self.add_param("output_load", unit="Ohm")
		self.add_param("output_polarity", unit="")

class ArbitraryWaveformGeneratorState(InstrumentState):
	
	__state_fields__ = ("first_channel", "num_channels", "channels")
	
	def __init__(self, first_channel:int, num_channels:int, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		
		self.add_param("channels", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=AWGChannelState, log=log))
		
		for ch_no in self.channels.get_range():
			self.channels[ch_no] = AWGChannelState(log=log)

class ArbitraryWaveformGenerator(Driver):
	
	WAVE_SINE = "wave-sine"
	WAVE_SQUARE = "wave-square"
	WAVE_RAMP = "wave-ramp"
	WAVE_PULSE = "wave-pulse"
	WAVE_NOISE = "wave-noise"
	WAVE_ARB = "wave-arb"
	WAVE_DC = "wave-dc"
	
	POLARITY_NORMAL = "polarity-normal"
	POLARITY_INVERTED = "polarity-inverted"
	
	# Output load is in ohms, except for the high-impedance setting, which is a distinct mode on
	# every generator rather than "a very large number" - so it gets a sentinel rather than
	# math.inf. inf would also be a serialization hazard: json.dumps writes it as `Infinity`,
	# which is not valid JSON, and this state crosses JSON boundaries over the network layer.
	LOAD_HIGH_Z = "load-high-z"
	
	# Waveforms for which a duty cycle exists at all. Setting one while a sine is in force is an
	# error on the instrument, not a no-op, which is why apply_state() has to be careful.
	DUTY_CYCLE_WAVEFORMS = (WAVE_SQUARE, WAVE_PULSE)
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="", first_channel:int=1, max_channels:int=2, dummy:bool=False, **kwargs):
		
		_state = ArbitraryWaveformGeneratorState(first_channel, max_channels, log=log)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy, first_channel_num=first_channel, **kwargs)
		
		self.max_channels = max_channels
		
		if self.dummy:
			self.init_dummy_state()
	
	def init_dummy_state(self):
		
		for ch_no in self.state.channels.get_range():
			self.set_waveform(ch_no, self.WAVE_SINE)
			self.set_frequency(ch_no, 1e3)
			self.set_amplitude(ch_no, 1)
			self.set_offset(ch_no, 0)
			self.set_phase(ch_no, 0)
			self.set_duty_cycle(ch_no, 50)
			self.set_output_enable(ch_no, False)
			self.set_output_load(ch_no, self.LOAD_HIGH_Z)
			self.set_output_polarity(ch_no, self.POLARITY_NORMAL)
	
	@abstractmethod
	def set_waveform(self, channel:int, wave:str):
		self.modify_state(lambda: self.get_waveform(channel), ["channels", "waveform_type"], wave, indices=[channel])
	
	@abstractmethod
	def get_waveform(self, channel:int):
		return self.modify_state(None, ["channels", "waveform_type"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_frequency(self, channel:int, freq_hz:float):
		self.modify_state(lambda: self.get_frequency(channel), ["channels", "frequency"], freq_hz, indices=[channel])
	
	@abstractmethod
	def get_frequency(self, channel:int):
		return self.modify_state(None, ["channels", "frequency"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_amplitude(self, channel:int, amplitude_Vpp:float):
		self.modify_state(lambda: self.get_amplitude(channel), ["channels", "amplitude"], amplitude_Vpp, indices=[channel])
	
	@abstractmethod
	def get_amplitude(self, channel:int):
		return self.modify_state(None, ["channels", "amplitude"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_offset(self, channel:int, offset_V:float):
		self.modify_state(lambda: self.get_offset(channel), ["channels", "offset"], offset_V, indices=[channel])
	
	@abstractmethod
	def get_offset(self, channel:int):
		return self.modify_state(None, ["channels", "offset"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_phase(self, channel:int, phase_deg:float):
		self.modify_state(lambda: self.get_phase(channel), ["channels", "phase"], phase_deg, indices=[channel])
	
	@abstractmethod
	def get_phase(self, channel:int):
		return self.modify_state(None, ["channels", "phase"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_duty_cycle(self, channel:int, duty_pct:float):
		''' Duty cycle in percent. Only meaningful for the waveforms in DUTY_CYCLE_WAVEFORMS -
		asking a generator for the duty cycle of a sine is an error on the instrument, not a
		harmless no-op. '''
		self.modify_state(lambda: self.get_duty_cycle(channel), ["channels", "duty_cycle"], duty_pct, indices=[channel])
	
	@abstractmethod
	def get_duty_cycle(self, channel:int):
		return self.modify_state(None, ["channels", "duty_cycle"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_output_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_output_enable(channel), ["channels", "output_enable"], enable, indices=[channel])
	
	@abstractmethod
	def get_output_enable(self, channel:int):
		return self.modify_state(None, ["channels", "output_enable"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_output_load(self, channel:int, load_ohm):
		''' Termination the generator assumes downstream, in ohms, or LOAD_HIGH_Z.
		
		This is not a cosmetic setting: it rescales what the instrument means by an amplitude. A
		generator set to 50 ohm outputs half the open-circuit voltage, so the SAME `set_amplitude`
		call produces a different physical signal depending on this value.
		'''
		self.modify_state(lambda: self.get_output_load(channel), ["channels", "output_load"], load_ohm, indices=[channel])
	
	@abstractmethod
	def get_output_load(self, channel:int):
		return self.modify_state(None, ["channels", "output_load"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_output_polarity(self, channel:int, polarity:str):
		self.modify_state(lambda: self.get_output_polarity(channel), ["channels", "output_polarity"], polarity, indices=[channel])
	
	@abstractmethod
	def get_output_polarity(self, channel:int):
		return self.modify_state(None, ["channels", "output_polarity"], self._super_hint, indices=[channel])
	
	def apply_state(self):
		''' Pushes the tracked state back out to the instrument.
		
		Two things are skipped rather than pushed blindly:
		
		- **`None` values.** A parameter that was never read or set has no value to apply, and
		  writing one produces literal garbage on the wire (`FRQ,None`). Anything still None is
		  something this driver has never known, so there is nothing to restore.
		- **Duty cycle on a waveform that has none.** Sending a duty cycle while a sine is in
		  force is an instrument error. The waveform is applied first, so the check is made
		  against the waveform actually being restored.
		'''
		
		for ch_no in self.state.channels.get_range():
			
			def _apply(setter_name, param):
				
				value = self.state.get(["channels", param], indices=[ch_no])
				if value is None:
					return
				
				getattr(self, setter_name)(ch_no, value)
			
			_apply("set_waveform", "waveform_type")
			_apply("set_frequency", "frequency")
			_apply("set_amplitude", "amplitude")
			_apply("set_offset", "offset")
			_apply("set_phase", "phase")
			
			if self.state.get(["channels", "waveform_type"], indices=[ch_no]) in self.DUTY_CYCLE_WAVEFORMS:
				_apply("set_duty_cycle", "duty_cycle")
			
			_apply("set_output_load", "output_load")
			_apply("set_output_polarity", "output_polarity")
			
			# Output enable is applied last, so the channel is fully configured before anything
			# reaches the connector.
			_apply("set_output_enable", "output_enable")
	
	def refresh_state(self):
		
		for ch_no in self.state.channels.get_range():
			self.get_waveform(ch_no)
			self.get_frequency(ch_no)
			self.get_amplitude(ch_no)
			self.get_offset(ch_no)
			self.get_phase(ch_no)
			self.get_duty_cycle(ch_no)
			self.get_output_enable(ch_no)
			self.get_output_load(ch_no)
			self.get_output_polarity(ch_no)
		
	def refresh_data(self):
		pass
