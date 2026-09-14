''' Category class for audio analyzers.

An audio analyzer pairs a low-distortion signal generator with a high-resolution ADC and analyzes a
captured buffer: spectrum, THD, THD+N, SNR, band-limited level. send_manual_trigger() captures one
buffer on every channel (driving the enabled generators while it does), and the data and measurement
getters report on the most recent capture. The data getters accept driver-specific options through
**kwargs - for example whether to capture first - documented by each driver.

Channels and generators are 1-indexed. A driver whose instrument names its channels declares
CHANNEL_NAMES, and every `channel` argument then accepts the name as well as the index.

Waveforms and spectra use the x/y dict shape {"x": [...], "y": [...], "x_units": str, "y_units": str},
as the spectrum analyzer's traces do.
'''

import numpy as np

from constellation.base import *


class AudioAnalyzerChannelState(InstrumentState):
	''' One input channel's most recent data, as last returned by get_waveform()/get_spectrum(). '''

	__state_fields__ = ("waveform", "spectrum")

	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)

		self.add_param("waveform", unit="", is_data=True, value=None)
		self.add_param("spectrum", unit="", is_data=True, value=None)


class AudioGeneratorState(InstrumentState):

	__state_fields__ = ("enable", "freq", "amplitude")

	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)

		self.add_param("enable", unit="bool")
		self.add_param("freq", unit="Hz")
		self.add_param("amplitude", unit="dBV")


class AudioAnalyzerState(InstrumentState):

	__state_fields__ = ("first_channel", "num_channels", "first_generator", "num_generators", "sample_rate", "buffer_size", "input_range", "window", "channels", "generators")

	def __init__(self, first_channel:int=1, num_channels:int=2, first_generator:int=1, num_generators:int=2, log:plf.LogPile=None):
		super().__init__(log=log)

		self.add_param("first_channel", unit="1", value=first_channel)
		self.add_param("num_channels", unit="1", value=num_channels)
		self.add_param("first_generator", unit="1", value=first_generator)
		self.add_param("num_generators", unit="1", value=num_generators)

		self.add_param("sample_rate", unit="Hz")
		self.add_param("buffer_size", unit="1")
		self.add_param("input_range", unit="dBV")
		self.add_param("window", unit="CONST")

		self.add_param("channels", unit="", value=IndexedList(first_channel, num_channels, validate_type=AudioAnalyzerChannelState, log=log))
		for ch_no in self.channels.get_range():
			self.channels[ch_no] = AudioAnalyzerChannelState(log=log)

		self.add_param("generators", unit="", value=IndexedList(first_generator, num_generators, validate_type=AudioGeneratorState, log=log))
		for gen_no in self.generators.get_range():
			self.generators[gen_no] = AudioGeneratorState(log=log)


class AudioAnalyzer(Driver):

	# {channel index: name}, for a driver whose instrument names its channels. Empty = index only.
	CHANNEL_NAMES = {}

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, expected_idn:str="", dummy:bool=False, num_channels:int=2, num_generators:int=2, **kwargs):

		# Synthetic capture used by dummy mode. Set before super().__init__(), which connects.
		self._dummy_capture = None

		_state = AudioAnalyzerState(first_channel=1, num_channels=num_channels, first_generator=1, num_generators=num_generators, log=log)
		super().__init__(address, log, relay, _state, expected_idn=expected_idn, dummy=dummy, first_channel_num=1, **kwargs)

		if self.dummy:
			self.init_dummy_state()

	def channel_index(self, channel, quiet:bool=False) -> int:
		''' Resolves a channel given by index (1, "1") or by name (case-insensitive, from
		CHANNEL_NAMES) to its index.

		Args:
			channel (int|str): Channel index or name.
			quiet (bool): Don't log an invalid channel - for a caller that has already reported it.

		Returns:
			int: The channel index, or None if there is no such channel.
		'''

		valid = list(self.state.channels.get_range())

		if isinstance(channel, str):
			text = channel.strip()
			for index, name in self.CHANNEL_NAMES.items():
				if text.lower() == name.lower():
					return index
			if text.isdigit():
				channel = int(text)

		if isinstance(channel, (int, np.integer)) and not isinstance(channel, bool) and int(channel) in valid:
			return int(channel)

		if not quiet:
			names = [self.CHANNEL_NAMES[i] for i in valid if i in self.CHANNEL_NAMES]
			self.error(f"Invalid channel >{channel}<. Valid channels: {valid}" + (f", or by name {names}" if names else "") + ".")
		return None

	# ==================================================================================
	# Settings

	@abstractmethod
	def set_sample_rate(self, rate_Hz:int):
		self.modify_state(self.get_sample_rate, ["sample_rate"], rate_Hz)

	@abstractmethod
	def get_sample_rate(self):
		return self.modify_state(None, ["sample_rate"], self._super_hint)

	@abstractmethod
	def set_buffer_size(self, size:int):
		''' Samples per acquisition, which is also the FFT length. '''
		self.modify_state(self.get_buffer_size, ["buffer_size"], size)

	@abstractmethod
	def get_buffer_size(self):
		return self.modify_state(None, ["buffer_size"], self._super_hint)

	@abstractmethod
	def set_input_range(self, range_dBV:float):
		''' Input full-scale level. '''
		self.modify_state(self.get_input_range, ["input_range"], range_dBV)

	@abstractmethod
	def get_input_range(self):
		return self.modify_state(None, ["input_range"], self._super_hint)

	@abstractmethod
	def set_window(self, window:str):
		''' FFT window applied to spectra and measurements, e.g. "Hann". '''
		self.modify_state(self.get_window, ["window"], window)

	@abstractmethod
	def get_window(self):
		return self.modify_state(None, ["window"], self._super_hint)

	@abstractmethod
	def set_generator_enable(self, generator:int, enable:bool):
		''' Whether the generator plays during an acquisition. '''
		self.modify_state(lambda: self.get_generator_enable(generator), ["generators", "enable"], enable, indices=[generator])

	@abstractmethod
	def get_generator_enable(self, generator:int):
		return self.modify_state(None, ["generators", "enable"], self._super_hint, indices=[generator])

	@abstractmethod
	def set_generator_freq(self, generator:int, freq_Hz:float):
		self.modify_state(lambda: self.get_generator_freq(generator), ["generators", "freq"], freq_Hz, indices=[generator])

	@abstractmethod
	def get_generator_freq(self, generator:int):
		return self.modify_state(None, ["generators", "freq"], self._super_hint, indices=[generator])

	@abstractmethod
	def set_generator_amplitude(self, generator:int, amplitude_dBV:float):
		self.modify_state(lambda: self.get_generator_amplitude(generator), ["generators", "amplitude"], amplitude_dBV, indices=[generator])

	@abstractmethod
	def get_generator_amplitude(self, generator:int):
		return self.modify_state(None, ["generators", "amplitude"], self._super_hint, indices=[generator])

	# ==================================================================================
	# Triggering and data

	@abstractmethod
	@enabledummy
	def send_manual_trigger(self):
		''' Captures one buffer on every channel, driving the enabled generators while it does, and
		blocks until the capture is complete.

		Returns:
			bool: True if the capture completed.
		'''
		return self._super_hint

	@abstractmethod
	@enabledummy
	def get_waveform(self, channel, **kwargs):
		''' Time-domain samples of the most recent capture: {"x": seconds, "y": volts, ...}. '''

		index = self.channel_index(channel, quiet=True)
		if index is None:
			return None
		return self.modify_state(None, ["channels", "waveform"], self._super_hint, indices=[index])

	@abstractmethod
	@enabledummy
	def get_spectrum(self, channel, **kwargs):
		''' Magnitude spectrum of the most recent capture, per FFT bin: {"x": Hz, "y": dBV, ...}. '''

		index = self.channel_index(channel, quiet=True)
		if index is None:
			return None
		return self.modify_state(None, ["channels", "spectrum"], self._super_hint, indices=[index])

	@abstractmethod
	@enabledummy
	def get_rms_level(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		''' RMS level within [f_lo_Hz, f_hi_Hz], in dBV. '''
		return self._super_hint

	@abstractmethod
	@enabledummy
	def get_peak_level(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		''' Peak level within [f_lo_Hz, f_hi_Hz], in dBV. '''
		return self._super_hint

	@abstractmethod
	@enabledummy
	def get_peak_freq(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		''' Frequency of the strongest spectral bin within [f_lo_Hz, f_hi_Hz], in Hz. '''
		return self._super_hint

	@abstractmethod
	@enabledummy
	def get_thd(self, channel, fund_Hz:float=1000.0, max_Hz:float=20000.0, **kwargs):
		''' Total harmonic distortion in dB relative to the fundamental, counting every harmonic
		up to max_Hz. '''
		return self._super_hint

	@abstractmethod
	@enabledummy
	def get_thdn(self, channel, fund_Hz:float=1000.0, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		''' THD+N in dB relative to the fundamental, over [f_lo_Hz, f_hi_Hz]. '''
		return self._super_hint

	@abstractmethod
	@enabledummy
	def get_snr(self, channel, fund_Hz:float=1000.0, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		''' Signal-to-noise ratio in dB, over [f_lo_Hz, f_hi_Hz]. '''
		return self._super_hint

	# ==================================================================================
	# Driver framework hooks

	def refresh_state(self):
		self.get_sample_rate()
		self.get_buffer_size()
		self.get_input_range()
		self.get_window()

		for gen_no in self.state.generators.get_range():
			self.get_generator_enable(gen_no)
			self.get_generator_freq(gen_no)
			self.get_generator_amplitude(gen_no)

	def apply_state(self):
		for name in ("sample_rate", "buffer_size", "input_range", "window"):
			value = self.state.get([name])
			if value is not None:
				getattr(self, f"set_{name}")(value)

		# Frequency and amplitude before enable, so a generator never plays a stale tone.
		for gen_no, gen in self.state.generators.populated_items():
			if gen.freq is not None:
				self.set_generator_freq(gen_no, gen.freq)
			if gen.amplitude is not None:
				self.set_generator_amplitude(gen_no, gen.amplitude)
			if gen.enable is not None:
				self.set_generator_enable(gen_no, gen.enable)

	def refresh_data(self):
		for ch_no in self.state.channels.get_range():
			self.get_spectrum(ch_no)
			self.get_waveform(ch_no)

	# ==================================================================================
	# Dummy support

	def init_dummy_state(self) -> None:
		''' A plausible starting configuration, with the first generator on at 1 kHz, -10 dBV so a
		dummy capture has a tone to analyze. '''

		self.set_sample_rate(48000)
		self.set_buffer_size(32768)
		self.set_input_range(18)
		self.set_window("Hann")

		for gen_no in self.state.generators.get_range():
			self.set_generator_freq(gen_no, 1000.0 * gen_no)
			self.set_generator_amplitude(gen_no, -10.0)
			self.set_generator_enable(gen_no, gen_no == self.state.first_generator)

	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Supplies SYNTHETIC dummy values only - see Oscilloscope.dummy_responder. Plain
		set_*/get_* methods are handled generically by modify_state() and need no case here.
		'''

		try:
			match func_name:
				case "send_manual_trigger":
					return self._dummy_trigger()
				case "get_waveform":
					return self._dummy_waveform(*args, **kwargs)
				case "get_spectrum":
					return self._dummy_spectrum(*args, **kwargs)
				case "get_rms_level":
					return self._dummy_rms_level(*args, **kwargs)
				case "get_peak_level":
					return self._dummy_peak_level(*args, **kwargs)
				case "get_peak_freq":
					return self._dummy_peak_freq(*args, **kwargs)
				case "get_thd":
					return self._dummy_thd(*args, **kwargs)
				case "get_thdn":
					return self._dummy_thdn(*args, **kwargs)
				case "get_snr":
					return self._dummy_snr(*args, **kwargs)
				case _:
					return super().dummy_responder(func_name, *args, **kwargs)
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction >{func_name}<. ({e})")
			return None

	def _dummy_trigger(self) -> bool:
		''' Synthesizes one capture from the tracked generator settings: each enabled generator as a
		bin-centred sine with 2nd/3rd harmonics at -90/-95 dBc, plus a noise floor, clipped at the
		input range. Channel 2 onward reads 6 dB lower than channel 1, so channels are distinguishable. '''

		fs = float(self.state.sample_rate or 48000)
		n = int(self.state.buffer_size or 32768)
		t = np.arange(n) / fs

		tone = np.zeros(n)
		for gen_no, gen in self.state.generators.populated_items():
			if not gen.enable or gen.freq is None or gen.amplitude is None:
				continue
			f = round(gen.freq * n / fs) * fs / n
			if not 0 < f < fs / 2:
				continue
			peak_V = np.sqrt(2) * 10**(gen.amplitude / 20)
			tone += peak_V * np.sin(2 * np.pi * f * t)
			for harmonic, rel_dB in ((2, -90.0), (3, -95.0)):
				if harmonic * f < fs / 2:
					tone += peak_V * 10**(rel_dB / 20) * np.sin(2 * np.pi * harmonic * f * t)

		rng = np.random.default_rng()
		channels = {}
		for ch_no in self.state.channels.get_range():
			gain = 1.0 if ch_no == self.first_channel else 0.5
			samples = gain * tone + rng.normal(0.0, 3e-6, n)
			if self.state.input_range is not None:
				limit = np.sqrt(2) * 10**(self.state.input_range / 20)
				samples = np.clip(samples, -limit, limit)
			channels[ch_no] = samples

		self._dummy_capture = {"sample_rate": fs, "channels": channels}
		return True

	def _dummy_samples(self, channel):
		index = self.channel_index(channel)
		if index is None:
			return None, None
		if self._dummy_capture is None:
			self.error("No capture yet - call send_manual_trigger() first.")
			return None, None
		return index, self._dummy_capture["channels"][index]

	def _dummy_bins(self, channel):
		''' (frequencies in Hz, Vrms per bin) of the synthetic capture. No window: the synthetic
		tones are bin-centred, so there is no leakage for one to suppress. '''

		_, samples = self._dummy_samples(channel)
		if samples is None:
			return None, None

		n = len(samples)
		fs = self._dummy_capture["sample_rate"]
		vrms = np.abs(np.fft.rfft(samples))[:n // 2] * np.sqrt(2) / n
		return np.arange(n // 2) * fs / n, vrms

	@staticmethod
	def _band_power(freqs, vrms, f_lo_Hz, f_hi_Hz):
		band = (freqs >= f_lo_Hz) & (freqs <= f_hi_Hz)
		return float(np.sum(vrms[band]**2))

	@staticmethod
	def _tone_power(freqs, vrms, f_Hz):
		''' Power in the bin nearest f_Hz and its two neighbours. '''
		k = int(round(f_Hz / (freqs[1] - freqs[0])))
		return float(np.sum(vrms[max(k - 1, 0):k + 2]**2))

	@staticmethod
	def _harmonic_power(freqs, vrms, fund_Hz, max_Hz):
		return sum(AudioAnalyzer._tone_power(freqs, vrms, h * fund_Hz) for h in range(2, int(max_Hz // fund_Hz) + 1) if h * fund_Hz < freqs[-1])

	@staticmethod
	def _to_dB(power_ratio):
		return 10 * np.log10(max(power_ratio, 1e-30))

	def _dummy_waveform(self, channel, **kwargs):
		index, samples = self._dummy_samples(channel)
		if samples is None:
			return None
		fs = self._dummy_capture["sample_rate"]
		wav = {"x": (np.arange(len(samples)) / fs).tolist(), "y": samples.tolist(), "x_units": "s", "y_units": "V"}
		self.state.set(["channels", "waveform"], wav, indices=[index])
		return wav

	def _dummy_spectrum(self, channel, **kwargs):
		freqs, vrms = self._dummy_bins(channel)
		if freqs is None:
			return None
		spectrum = {"x": freqs.tolist(), "y": (20 * np.log10(np.maximum(vrms, 1e-12))).tolist(), "x_units": "Hz", "y_units": "dBV"}
		self.state.set(["channels", "spectrum"], spectrum, indices=[self.channel_index(channel)])
		return spectrum

	def _dummy_rms_level(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		freqs, vrms = self._dummy_bins(channel)
		if freqs is None:
			return None
		return self._to_dB(self._band_power(freqs, vrms, f_lo_Hz, f_hi_Hz))

	def _dummy_peak_level(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		freqs, vrms = self._dummy_bins(channel)
		if freqs is None:
			return None
		band = (freqs >= f_lo_Hz) & (freqs <= f_hi_Hz)
		return self._to_dB(float(np.max(vrms[band]))**2)

	def _dummy_peak_freq(self, channel, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		freqs, vrms = self._dummy_bins(channel)
		if freqs is None:
			return None
		band = (freqs >= f_lo_Hz) & (freqs <= f_hi_Hz)
		return float(freqs[band][np.argmax(vrms[band])])

	def _dummy_thd(self, channel, fund_Hz:float=1000.0, max_Hz:float=20000.0, **kwargs):
		freqs, vrms = self._dummy_bins(channel)
		if freqs is None:
			return None
		return self._to_dB(self._harmonic_power(freqs, vrms, fund_Hz, max_Hz) / self._tone_power(freqs, vrms, fund_Hz))

	def _dummy_thdn(self, channel, fund_Hz:float=1000.0, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		freqs, vrms = self._dummy_bins(channel)
		if freqs is None:
			return None
		fund = self._tone_power(freqs, vrms, fund_Hz)
		return self._to_dB((self._band_power(freqs, vrms, f_lo_Hz, f_hi_Hz) - fund) / fund)

	def _dummy_snr(self, channel, fund_Hz:float=1000.0, f_lo_Hz:float=20.0, f_hi_Hz:float=20000.0, **kwargs):
		freqs, vrms = self._dummy_bins(channel)
		if freqs is None:
			return None
		fund = self._tone_power(freqs, vrms, fund_Hz)
		noise = self._band_power(freqs, vrms, f_lo_Hz, f_hi_Hz) - fund - self._harmonic_power(freqs, vrms, fund_Hz, f_hi_Hz)
		return self._to_dB(fund / noise)
