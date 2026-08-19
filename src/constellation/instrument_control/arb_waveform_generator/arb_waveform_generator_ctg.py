from constellation.base import *

class AWGChannelState(InstrumentState):
	
	__state_fields__ = ("waveform_type", "frequency", "amplitude", "offset", "output_enable")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("waveform_type", unit="")
		self.add_param("frequency", unit="Hz")
		self.add_param("amplitude", unit="Vpp")
		self.add_param("offset", unit="V")
		self.add_param("output_enable", unit="Hz")
		
		self.validate()

class ArbitraryWaveformGeneratorState(InstrumentState):
	
	__state_fields__ = ("first_channel", "num_channels", "channels")
	
	def __init__(self, log:plf.LogPile=None, first_channel:int=1, num_channels:int=2):
		super().__init__(log=log)
		
		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		
		self.add_param("channels", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=AWGChannelState, log=log))
		
		for ch_no in self.channels.get_range():
			self.channels[ch_no] = AWGChannelState(log=log)
		
		self.validate()

class ArbitraryWaveformGenerator(Driver):
	
	WAVE_SINE = "wave-sine"
	WAVE_SQUARE = "wave-square"
	WAVE_RAMP = "wave-ramp"
	WAVE_PULSE = "wave-pulse"
	WAVE_NOISE = "wave-noise"
	WAVE_ARB = "wave-arb"
	WAVE_DC = "wave-dc"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay, expected_idn:str="", dummy:bool=False, max_channels:int=2):
		
		_state = ArbitraryWaveformGeneratorState(log, 1, max_channels)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy)
		
		self.max_channels = max_channels
		
		if self.dummy:
			self.init_dummy_state()
	
	def init_dummy_state(self):
		
		for ch_no in self.state.channels.get_range():
			self.set_waveform(ch_no, self.WAVE_SINE)
			self.set_frequency(ch_no, 1e3)
			self.set_amplitude(ch_no, 1)
			self.set_offset(ch_no, 0)
			self.set_output_enable(ch_no, False)
	
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
	def set_output_enable(self, channel:int, enable:bool):
		self.modify_state(lambda: self.get_output_enable(channel), ["channels", "output_enable"], enable, indices=[channel])
	
	@abstractmethod
	def get_output_enable(self, channel:int):
		return self.modify_state(None, ["channels", "output_enable"], self._super_hint, indices=[channel])
	
	def apply_state(self):
		
		for ch_no in self.state.channels.get_range():
			self.set_waveform(ch_no, self.state.get(["channels", "waveform_type"], indices=[ch_no]))
			self.set_frequency(ch_no, self.state.get(["channels", "frequency"], indices=[ch_no]))
			self.set_amplitude(ch_no, self.state.get(["channels", "amplitude"], indices=[ch_no]))
			self.set_offset(ch_no, self.state.get(["channels", "offset"], indices=[ch_no]))
			self.set_output_enable(ch_no, self.state.get(["channels", "output_enable"], indices=[ch_no]))
	
	def refresh_state(self):
		
		for ch_no in self.state.channels.get_range():
			self.get_waveform(ch_no)
			self.get_frequency(ch_no)
			self.get_amplitude(ch_no)
			self.get_offset(ch_no)
			self.get_output_enable(ch_no)
		
	def refresh_data(self):
		pass