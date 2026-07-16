"""RIGOL’s 1000Z Series Digital Oscilloscope

https://beyondmeasure.rigoltech.com/acton/attachment/1579/f-0386/1/-/-/-/-/DS1000Z_Programming%20Guide_EN.pdf
"""

from constellation.instrument_control.oscilloscope.oscilloscope_ctg import *

# Maximum number of waveform points the DS1000Z will return per single :WAV:DATA? query, per the
# programming guide's :WAVeform:DATA? section (WORD's 125000 isn't used - this driver only
# supports BYTE/ASCii transfer). :WAV:STARt/:WAV:STOP's valid range in RAW mode is "1 to the
# current memory depth" - exceeding it is rejected by the instrument (confirmed: an earlier
# version of this driver sent an arbitrary large :WAV:STOP assuming it would be clamped, which
# instead produced on-screen SCPI errors) - so the actual memory depth must always be queried via
# :ACQuire:MDEPth? rather than guessed, and every :WAV:STOP value sent must stay within it.
_WAV_MAX_CHUNK_POINTS = {True: 250000, False: 15625}  # keyed by `binary`

def _parse_wav_preamble(preamble_str:str):
	''' Parses a Rigol :WAVeform:PREamble? response into the fields get_waveform() needs.

	Format (comma-separated): format, type, points, count, xincrement, xorigin, xreference,
	yincrement, yorigin, yreference - see the DS1000Z programming guide's :WAVeform:PREamble?
	section (linked at the top of this file).
	'''
	parts = preamble_str.strip().split(",")
	points = int(float(parts[2]))
	xincrement = float(parts[4])
	xorigin = float(parts[5])
	yincrement = float(parts[7])
	yorigin = float(parts[8])
	yreference = float(parts[9])
	return points, xincrement, xorigin, yincrement, yorigin, yreference

class RigolDS1000Z(Oscilloscope, MeasurementsMixin):

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=DirectSCPIRelay(), max_channels:int=4, **kwargs):
		super().__init__(address, log, relay=relay, expected_idn='RIGOL TECHNOLOGIES,DS10', max_channels=max_channels, num_div_horiz=12, num_div_vert=8, **kwargs)
		
		# Table to translate mixin constants to SCPI measurement strings
		self.meas_table = {MeasurementsMixin.MEAS_VMAX:'VMAX', MeasurementsMixin.MEAS_VMIN:'VMIN', MeasurementsMixin.MEAS_VAVG:'VAVG', MeasurementsMixin.MEAS_VPP:'VPP', MeasurementsMixin.MEAS_FREQ:'FREQ'}
		
		# Table to translate mixin constants to SCPI statistics strings
		self.stat_table = {MeasurementsMixin.STAT_AVG:'AVER', MeasurementsMixin.STAT_MAX:'MAX', MeasurementsMixin.STAT_MIN:'MIN', MeasurementsMixin.STAT_CURR:'CURR', MeasurementsMixin.STAT_STD:'DEV'}
		
		# Table to translate coupling constants to SCPI strings
		self.coupling_table = {Oscilloscope.COUPLING_AC:"AC", Oscilloscope.COUPLING_DC:"DC", Oscilloscope.COUPLING_GND:"GND"}
		
	# def set_div_time(self, time_s:float):
	# 	self.write(f":TIM:MAIN:SCAL {time_s}")
	# 	super().set_div_time(time_s)
	
	@superreturn
	def set_coupling(self, channel:int, coupling:str):
		
		# Validate input
		if coupling not in self.coupling_table.keys():
			self.warning(f"Cannot set coupling to unrecognized value '{coupling}'.")
			return
		
		coupling_code = self.coupling_table[coupling]
		
		self.write(f":CHAN{channel}:COUP {coupling_code}")
	
	@superreturn
	def get_coupling(self, channel:int):
		rval =  self.query(f":CHAN{channel}:COUP?").strip()
		
		inverted = {v: k for k, v in self.coupling_table.items()}
		if rval not in inverted:
			self.error(f"Received unrecognized coupling mode >@:LOCK'{rval}'@:UNLOCK< from instrument.", detail=f"Coupling options include: {inverted}.")
			return
		self._super_hint = inverted[rval]
		
	
	@superreturn
	def set_div_time(self, time_s:float):
		self.write(f":TIM:MAIN:SCAL {time_s}")
		
	@superreturn
	def get_div_time(self):
		self._super_hint = float(self.query(f":TIM:MAIN:SCAL?"))
	
	@superreturn
	def set_offset_time(self, time_s:float):
		self.write(f":TIM:MAIN:OFFS {time_s}")
	
	@superreturn
	def get_offset_time(self):
		self._super_hint = float(self.query(f":TIM:MAIN:OFFS?"))
	
	@superreturn
	def set_div_volt(self, channel:int, volt_V:float):
		self.write(f":CHAN{channel}:SCAL {volt_V}")
	
	@superreturn
	def get_div_volt(self, channel:int):
		self._super_hint = float(self.query(f":CHAN{channel}:SCAL?"))
	
	@superreturn
	def set_offset_volt(self, channel:int, volt_V:float):
		self.write(f":CHAN{channel}:OFFS {volt_V}")
	
	@superreturn
	def get_offset_volt(self, channel:int):
		self._super_hint = float(self.query(f":CHAN{channel}:OFFS?"))
	
	@superreturn
	def set_chan_enable(self, channel:int, enable:bool):
		self.write(f":CHAN{channel}:DISP {bool_to_str01(enable)}")
	
	@superreturn
	def get_chan_enable(self, channel:int):
		self._super_hint = str_to_bool(self.query(f":CHAN{channel}:DISP?"))
	
	@superreturn
	def set_probe_attenuation(self, channel:int, attenuation:float):
		valid_probe_attenuations = {0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500, 1000}
		if attenuation not in valid_probe_attenuations:
			self.error(f"Probe attenuation >{attenuation}< not supported. Valid options: {sorted(valid_probe_attenuations)}")
			return
		self.write(f":CHAN{channel}:PROB {attenuation}")
	
	@superreturn
	def get_probe_attenuation(self, channel:int):
		self._super_hint =  float(self.query(f":CHAN{channel}:PROB?"))
	
	@superreturn
	def set_bandwidth_limit(self, channel:int, enable:bool):
		enable_code = "OFF"
		if enable:
			enable_code = "20M"
		self.write(f":CHAN{channel}:BWL {enable_code}")
	
	@superreturn
	def get_bandwidth_limit(self, channel:int):
		resp = self.query(f":CHAN{channel}:BWL?")
		if resp is None:
			self._super_hint = None
			return
		self._super_hint = resp.strip().upper() in ["1", "ON", "20M"]
	
	@superreturn
	def set_trigger_mode(self, mode:str):
		
		mode_table = {Oscilloscope.TRIG_SINGLE:"SING", Oscilloscope.TRIG_AUTO:"AUTO", Oscilloscope.TRIG_NORM:"NORM"}
		if mode not in mode_table:
			self.error(f"Cannot set trigger mode >{mode}<. Mode not recognized.")
			return
		
		self.write(f":TRIG:EDGE:SWE {mode_table[mode]}")
	
	@superreturn
	def get_trigger_mode(self):
		
		# Get value from scope
		mode = self.query(":TRIG:EDGE:SWE?").strip()
		
		mode_table = {Oscilloscope.TRIG_SINGLE:"SING", Oscilloscope.TRIG_AUTO:"AUTO", Oscilloscope.TRIG_NORM:"NORM"}
		inverted = {v: k for k, v in mode_table.items()}
		
		if mode not in inverted:
			self.error(f"Cannot set trigger mode >{mode}<. Mode not recognized.")
			return
		self._super_hint = inverted[mode]
	
	@superreturn
	def set_trigger_level(self, level_V:float):
		self.write(f":TRIG:EDGE:LEV {level_V}")

	@superreturn
	def get_trigger_level(self):
		self._super_hint = self.query(f":TRIG:EDGE:LEV?")
	
	@superreturn
	def set_trigger_source(self, channel:int=None, external:bool=False, line:bool=False):
		
		# Get source string
		src_str = self._format_trigger_source(channel, external, line)
		
		self.write(f":TRIG:EDGE:SOUR {src_str}")
	
	@superreturn
	def get_trigger_source(self):
		
		src_str = self.query(f":TRIG:EDGE:SOUR?").strip()
		
		if src_str == "CHAN1":
			self._super_hint = "1"
		elif src_str == "CHAN2":
			self._super_hint = "2"
		elif src_str == "CHAN3":
			self._super_hint = "3"
		elif src_str == "CHAN4":
			self._super_hint = "4"
		elif src_str == "EXT":
			self._super_hint = "EXT"
		elif src_str == "AC":
			self._super_hint = "LINE"
		else:
			self.warning(f"Unrecognized trigger source string. >@LOCK{src_str}@UNLOCK<")
			self._super_hint = src_str
	
	@superreturn
	def run_acquisition(self):
		self.write(f":RUN")
	
	@superreturn
	def stop_acquisition(self):
		self.write(f":STOP")
	
	@superreturn
	def do_single_trigger(self):
		self.write(f":SING")
	
	@superreturn
	def do_force_trigger(self):
		self.write(f":TFORCE")
	
	@superreturn
	def get_waveform(self, channel:int, binary:bool=True, full_memory:bool=True, max_points:int=None, _skip_run_management:bool=False):
		''' Reads the waveform on the specified channel.

		By default this reads the *entire* acquisition memory (not just the ~1200 points shown
		on screen) using fast binary transfer. Both are new defaults: the previous implementation
		always used :WAV:MODE NORM (screen-only - silently truncating a deep-memory capture to a
		small fraction of what was actually acquired) and ASCII transfer (much slower than binary
		for a large record).

		Reading the full memory (full_memory=True) requires the acquisition to be stopped, since
		:WAV:MODE RAW is only valid while stopped - this function stops it first if necessary and
		resumes running afterward if it was running before the call.

		Args:
			channel: Channel to read.
			binary: If True (default), use fast binary (BYTE) transfer. If False, use the
				slower ASCII transfer (kept as an option for debugging/compatibility).
			full_memory: If True (default), read the entire acquisition record via :WAV:MODE RAW
				(briefly stopping/resuming acquisition as needed). If False, only reads what's
				currently displayed on screen via :WAV:MODE NORM - this matches the old behavior:
				doesn't touch run/stop state, and is faster for a quick look at a live trace.
			max_points: Optional cap on how many points to retrieve (starting from the first
				sample). Only meaningful when full_memory=True. None (default) retrieves
				everything the scope has.
			_skip_run_management: Internal use by get_all_waveforms(), which stops/resumes
				acquisition once for the whole batch of channels instead of once per channel -
				leave this alone when calling get_waveform() directly.

		Returns:
			dict with keys 'time_s', 'volt_V', 'channel'.
		'''

		was_running = False
		if full_memory and not _skip_run_management:
			was_running = self.query(":TRIGger:STATus?").strip().upper() != "STOP"
			if was_running:
				self.stop_acquisition()
				self._wait_for_trigger_status("STOP")

		volts, xincr, xorigin = [], 0.0, 0.0

		try:
			self.write(f":WAV:SOUR CHAN{channel}")
			self.write(f":WAV:MODE {'RAW' if full_memory else 'NORM'}")
			self.write(f":WAV:FORM {'BYTE' if binary else 'ASCII'}")

			# :WAV:STARt/:WAV:STOP's valid range in RAW mode is "1 to the current memory depth" -
			# sending a value outside that range is rejected by the instrument, so the actual
			# depth must be queried, never guessed. NORM mode's range is always documented as a
			# fixed 1-1200 (the screen's point count).
			total_points = self._get_memory_depth() if full_memory else 1200

			if max_points is not None:
				total_points = min(total_points, max_points)

			chunk_cap = _WAV_MAX_CHUNK_POINTS[binary]

			# Query the waveform scaling factors once via the preamble, using a first chunk range
			# that's guaranteed to be within [1, total_points] so it can't itself be rejected.
			self.write(":WAV:STAR 1")
			self.write(f":WAV:STOP {min(chunk_cap, total_points) if total_points > 0 else 1}")
			_, xincr, xorigin, yincr, yorigin, yref = _parse_wav_preamble(self.query(":WAV:PRE?"))

			# The scope caps how many points it returns per single :WAV:DATA? query (see
			# _WAV_MAX_CHUNK_POINTS) - read the full record (or max_points) in bounded batches,
			# each explicitly sized to stay within that cap and within total_points.
			start = 1
			while start <= total_points:

				stop = min(start + chunk_cap - 1, total_points)
				self.write(f":WAV:STAR {start}")
				self.write(f":WAV:STOP {stop}")

				if binary:
					codes = self.query_binary(":WAV:DATA?", datatype='B')
					chunk = [(code - yorigin - yref) * yincr for code in codes]
				else:
					data = self.query("WAV:DATA?")
					# Filter out empty/whitespace-only tokens - a trailing comma before the
					# terminating newline otherwise leaves a bare '\n' token float() can't parse.
					chunk = [float(v) for v in data[11:].split(",") if v.strip() != ""]

				if not chunk:
					break  # avoid an infinite loop if the instrument stops returning new data

				volts.extend(chunk)
				start += len(chunk)

		except Exception as e:
			self.error(f"Failed to read waveform on channel {channel}. ({e})")
			volts = []

		finally:
			# Always resume acquisition if we're the one who stopped it, even on failure.
			if was_running:
				self.run_acquisition()

		t = list(xorigin + np.linspace(0, xincr * (len(volts) - 1), len(volts))) if volts else []

		self._super_hint = {"time_s":t, "volt_V":volts, "channel":channel}

	def _get_memory_depth(self) -> int:
		''' Returns the oscilloscope's current memory depth in points (the valid upper bound for
		:WAV:STARt/:WAV:STOP in RAW mode). :ACQuire:MDEPth? returns either a concrete integer or
		the string "AUTO" if memory depth is set to auto; in the AUTO case, resolve it via the
		documented relationship Memory Depth = Sample Rate x Waveform Length (Waveform Length =
		timebase-per-division x 12 divisions on a DS1000Z).
		'''
		mdepth_str = self.query(":ACQuire:MDEPth?").strip()
		try:
			return int(float(mdepth_str))
		except ValueError:
			sample_rate = float(self.query(":ACQuire:SRATe?").strip())
			timebase_per_div = float(self.query(":TIM:MAIN:SCAL?").strip())
			return int(round(sample_rate * timebase_per_div * 12))

	def _begin_waveform_batch(self, full_memory:bool=True, **kwargs):
		''' Stops acquisition once for the whole get_all_waveforms() batch (if full_memory is
		requested and it isn't already stopped), instead of each channel's get_waveform() call
		independently stopping/resuming - besides being wasteful, resuming between channels means
		each channel's "full memory" read would come from a different acquisition/trigger event. '''

		if not full_memory:
			return False
		was_running = self.query(":TRIGger:STATus?").strip().upper() != "STOP"
		if was_running:
			self.stop_acquisition()
			self._wait_for_trigger_status("STOP")
		return was_running

	def _end_waveform_batch(self, batch_state, **kwargs):
		if batch_state:
			self.run_acquisition()

	def _wait_for_trigger_status(self, target:str, timeout_s:float=5.0, poll_interval_s:float=0.2):
		''' Polls :TRIGger:STATus? until it reports `target`, or `timeout_s` elapses.

		:STOP/:RUN do not take effect instantaneously - the oscilloscope needs a moment to
		actually complete the acquisition state transition. Proceeding immediately (e.g. straight
		into :WAV:MODE RAW, which requires the scope to actually be stopped) while it's still
		mid-transition was confirmed against real hardware to make the scope reject subsequent
		commands ("Cannot operate now!" on the DS1000Z's screen) and hang the query that follows
		until it times out.
		'''

		t0 = time.time()
		status = self.query(":TRIGger:STATus?").strip().upper()
		while status != target and time.time() - t0 < timeout_s:
			time.sleep(poll_interval_s)
			status = self.query(":TRIGger:STATus?").strip().upper()

		if status != target:
			self.warning(f"Timed out waiting for :TRIGger:STATus? to report >{target}< (last saw >{status}<).")

	@superreturn
	def add_measurement(self, channel:int, measurement:int):
		
		# Find measurement string
		if measurement not in self.meas_table:
			self.error(f"Cannot add measurement >{measurement}<. Measurement not recognized.")
			return
		item_str = self.meas_table[measurement]
		
		# Get channel string
		channel_val = max(1, min(channel, 4))
		if channel_val != channel:
			self.error("Channel must be between 1 and 4.")
			return
		src_str = f"CHAN{channel_val}"
		
		# Send message
		self.write(f":MEASURE:ITEM {item_str},{src_str}")
	
	@superreturn
	def get_measurement(self, channel:int, measurement:str, stat_mode:str=MeasurementsMixin.STAT_CURR) -> float:
		
		# FInd measurement string
		if measurement not in self.meas_table:
			self.log.error(f"Cannot add measurement >{measurement}<. Measurement not recognized.")
			return
		item_str = self.meas_table[measurement]
		
		# FInd stat mode string
		if stat_mode not in self.stat_table:
			self.log.error(f"Cannot use stat-mode >{stat_mode}<. Statistic code not recognized.")
			return
		stat_str = self.stat_table[stat_mode]
		
		# Get channel string
		channel = max(1, min(channel, 1000))
		if channel != channel:
			self.log.error("Channel must be between 1 and 4.")
			return
		src_str = f"CHAN{channel}"
			
		self._super_hint = float(self.query(f":MEASURE:STAT:ITEM? {stat_str},{item_str},{src_str}"))
	
	@superreturn
	def clear_measurements(self):
		
		self.write(f":MEASURE:CLEAR ALL")
	
	@superreturn
	def set_measurement_stat_display(self, enable:bool):
		'''
		Turns display statistical values on/off for the Rigol DS1000Z series scopes. Not
		part of the Oscilloscope, but local to this driver.
		
		Args:
			enable (bool): Turns displayed stats on/off
		
		Returns:
			None
		'''
		
		self.write(f":MEASure:STATistic:DISPlay {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_measurement_stat_display(self):
		''' Checks if the measuremnt statistics table is enabled
		'''
		
		self._super_hint = str_to_bool(self.query(f":MEASure:STATistic:DISPlay?"))
	