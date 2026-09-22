''' Microwave/RF signal sources - synthesized CW generators.

Instruments in this category produce **one sine wave per output**, specified in the frequency
domain: a frequency, a level in dBm, and an RF on/off. That is the whole point of the category
and the reason it is not a flavour of `ArbitraryWaveformGenerator`:

- there is no waveform shape to choose, so no `set_waveform`, no duty cycle, no arbitrary buffer;
- the level is a **power** in dBm into a matched (nearly always 50 ohm) load, not a peak-to-peak
  voltage into a selectable termination. A microwave source has no "high-Z" output mode to
  rescale an amplitude against, so `output_load` has no meaning here either;
- frequency ranges are GHz-scale, where an AWG's sample-rate-bound parameters are meaningless.

Covers single-output boxes (Rohde & Schwarz SGS100A / SGMA, HP-Agilent 83711B) and multi-output
ones (R&S SMW200A with a second RF path), so every setting is per channel and the channel count
is given at construction via `max_channels`.

**Modulation is deliberately absent.** AM/FM/phase/pulse modulation differ enormously between
sources and are optional on most of them - including both instruments named above, where pulse
modulation is an ordering option rather than a given. It belongs in a mixin designed against a
real modulating source, not in the common API every CW generator must implement.
'''

from constellation.base import *

class MicrowaveSourceChannelState(InstrumentState):
	
	__state_fields__ = ("frequency", "power", "power_offset", "phase", "alc_enable", "output_enable")
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("frequency", unit="Hz")
		self.add_param("power", unit="dBm")
		self.add_param("power_offset", unit="dB")
		self.add_param("phase", unit="deg")
		
		self.add_param("alc_enable", unit="bool")
		self.add_param("output_enable", unit="bool")

class MicrowaveSourceState(InstrumentState):
	
	__state_fields__ = ("first_channel", "num_channels", "reference_source", "channels")
	
	def __init__(self, first_channel:int, num_channels:int, log:plf.LogPile=None):
		super().__init__(log=log)
		
		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		
		# The reference oscillator feeds every synthesizer in the box, so it is one instrument-wide
		# setting rather than one per channel.
		self.add_param("reference_source", unit="")
		
		self.add_param("channels", unit="", value=IndexedList(self.first_channel, self.num_channels, validate_type=MicrowaveSourceChannelState, log=log))
		
		for ch_no in self.channels.get_range():
			self.channels[ch_no] = MicrowaveSourceChannelState(log=log)

class MicrowaveSource(Driver):
	
	# Where the 10 MHz timebase comes from. Which one is in force decides whether this source's
	# frequency is its own or the rack's, so it is tracked rather than left to the front panel.
	REF_INTERNAL = "ref-internal"
	REF_EXTERNAL = "ref-external"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="", first_channel:int=1, max_channels:int=1, dummy:bool=False, **kwargs):
		''' Args:
			max_channels (int): Number of RF outputs. Defaults to 1, since most microwave sources
				are single-output - unlike an AWG, where two is the norm.
		'''
		
		_state = MicrowaveSourceState(first_channel, max_channels, log=log)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy, first_channel_num=first_channel, **kwargs)
		
		self.max_channels = max_channels
		
		if self.dummy:
			self.init_dummy_state()
	
	def init_dummy_state(self):
		
		self.set_reference_source(self.REF_INTERNAL)
		
		for ch_no in self.state.channels.get_range():
			self.set_frequency(ch_no, 1e9)
			self.set_power(ch_no, -20)
			self.set_power_offset(ch_no, 0)
			self.set_phase(ch_no, 0)
			self.set_alc_enable(ch_no, True)
			
			# Off, deliberately: a dummy source that comes up transmitting teaches a habit that is
			# expensive when the same script is pointed at hardware wired to a receiver.
			self.set_output_enable(ch_no, False)
	
	@abstractmethod
	def set_reference_source(self, source:str):
		''' Selects the internal timebase or an external 10 MHz reference. '''
		self.modify_state(lambda: self.get_reference_source(), ["reference_source"], source)
	
	@abstractmethod
	def get_reference_source(self):
		return self.modify_state(None, ["reference_source"], self._super_hint)
	
	@abstractmethod
	def set_frequency(self, channel:int, freq_hz:float):
		''' CW output frequency in Hz. '''
		self.modify_state(lambda: self.get_frequency(channel), ["channels", "frequency"], freq_hz, indices=[channel])
	
	@abstractmethod
	def get_frequency(self, channel:int):
		return self.modify_state(None, ["channels", "frequency"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_power(self, channel:int, power_dBm:float):
		''' Output level in dBm.
		
		This is the level the instrument is asked to produce, which is the level *at the plane the
		offset describes* - see set_power_offset(). Setting a level above the source's rated
		maximum for the frequency in force is an instrument error, not a clamp, on most sources.
		'''
		self.modify_state(lambda: self.get_power(channel), ["channels", "power"], power_dBm, indices=[channel])
	
	@abstractmethod
	def get_power(self, channel:int):
		return self.modify_state(None, ["channels", "power"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_power_offset(self, channel:int, offset_dB:float):
		''' Constant offset, in dB, between the level the source generates and the level it
		reports and accepts.
		
		This is bookkeeping for the loss between the front panel connector and the plane the user
		cares about - typically cable and coupler loss. It changes nothing inside the instrument,
		but it does change what every `set_power` call means, so it belongs in tracked state and
		must be applied *before* a level, not after.
		'''
		self.modify_state(lambda: self.get_power_offset(channel), ["channels", "power_offset"], offset_dB, indices=[channel])
	
	@abstractmethod
	def get_power_offset(self, channel:int):
		return self.modify_state(None, ["channels", "power_offset"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_phase(self, channel:int, phase_deg:float):
		''' Phase offset of this output, in degrees, relative to the reference.
		
		Only meaningful on a source whose synthesizer is locked to something else - a second
		channel, or an external reference - which is why a good number of single-output CW
		generators cannot do this at all.
		'''
		self.modify_state(lambda: self.get_phase(channel), ["channels", "phase"], phase_deg, indices=[channel])
	
	@abstractmethod
	def get_phase(self, channel:int):
		return self.modify_state(None, ["channels", "phase"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_alc_enable(self, channel:int, enable:bool):
		''' Automatic level control on/off.
		
		With ALC on, the source servos its output against an internal detector and holds the
		level flat. With it off, the level is set open-loop from a calibration table - less
		accurate, but the only option when the output is pulsed or swept faster than the ALC
		loop can follow, since an ALC chasing a gated carrier produces level glitches.
		'''
		self.modify_state(lambda: self.get_alc_enable(channel), ["channels", "alc_enable"], enable, indices=[channel])
	
	@abstractmethod
	def get_alc_enable(self, channel:int):
		return self.modify_state(None, ["channels", "alc_enable"], self._super_hint, indices=[channel])
	
	@abstractmethod
	def set_output_enable(self, channel:int, enable:bool):
		''' RF output on/off. This is the only call in the category that puts power on a cable. '''
		self.modify_state(lambda: self.get_output_enable(channel), ["channels", "output_enable"], enable, indices=[channel])
	
	@abstractmethod
	def get_output_enable(self, channel:int):
		return self.modify_state(None, ["channels", "output_enable"], self._super_hint, indices=[channel])
	
	def apply_state(self):
		''' Pushes the tracked state back out to the instrument.
		
		`None` values are skipped rather than written: a parameter that has never been read or set
		has no value to restore, and writing one produces literal garbage on the wire
		(`:FREQ:CW None`).
		
		Order matters twice here. The power offset goes out before the level, because it changes
		what that level means. The RF output goes out last, so nothing is radiated while the
		channel is still half-configured - restoring a state should not sweep the output from the
		old frequency to the new one with the amplifier live.
		'''
		
		if self.state.reference_source is not None:
			self.set_reference_source(self.state.reference_source)
		
		for ch_no in self.state.channels.get_range():
			
			def _apply(setter_name, param):
				
				value = self.state.get(["channels", param], indices=[ch_no])
				if value is None:
					return
				
				getattr(self, setter_name)(ch_no, value)
			
			_apply("set_power_offset", "power_offset")
			_apply("set_frequency", "frequency")
			_apply("set_power", "power")
			_apply("set_phase", "phase")
			_apply("set_alc_enable", "alc_enable")
			
			_apply("set_output_enable", "output_enable")
	
	def refresh_state(self):
		
		self.get_reference_source()
		
		for ch_no in self.state.channels.get_range():
			self.get_frequency(ch_no)
			self.get_power(ch_no)
			self.get_power_offset(ch_no)
			self.get_phase(ch_no)
			self.get_alc_enable(ch_no)
			self.get_output_enable(ch_no)
	
	def refresh_data(self):
		pass
