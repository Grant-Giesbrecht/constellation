''' Driver for Rhode & Schwarz ZVA

Manual (Requires login and R&S approval): https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/dl_common_library/dl_manuals/gb_1/z/zva_2/ZVA_ZVB_ZVT_OperatingManual_en_33.pdf
'''

from constellation.base import *
from constellation.instrument_control.vector_network_analyzer.vector_network_analyzer_ctg import *
import array

class RohdeSchwarzZVA(BasicVectorNetworkAnalyzerCtg):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, relay=DirectSCPIRelay(), expected_idn="Rohde&Schwarz,ZVA")
		
		# This translates the string measurement codes defined the the BasicVectorNetworkAnalyzerCtg class
		# to strings that are understood by the specific instrument model (the ZVA).
		self.measurement_codes = {}
		self.measurement_codes[BasicVectorNetworkAnalyzerCtg.MEAS_S11] = "S11"
		self.measurement_codes[BasicVectorNetworkAnalyzerCtg.MEAS_S12] = "S12"
		self.measurement_codes[BasicVectorNetworkAnalyzerCtg.MEAS_S21] = "S21"
		self.measurement_codes[BasicVectorNetworkAnalyzerCtg.MEAS_S22] = "S22"
		
		self.format_table = {}
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_LOG_MAG] = "MLOG"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_PHASE] = "PHAS"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_SMITH] = "SMIT"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_POLAR] = "POL"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_SWR] = "SWR"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_LIN_MAG] = "MLIN"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_REAL] = "REAL"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_IMAG] = "IMAG"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_SMITH_INV] = "ISM"
		self.format_table[BasicVectorNetworkAnalyzerCtg.FORM_PHASE_UW] = "UPH"
	
	def _get_trace_idx(self, trace_name:str) -> int:
		''' Returns the index for the requested trace. Returns NOne if not found.
		'''
		
		for idx, tr in enumerate(self.traces):
			if tr.id_str == trace_name:
				return idx
		
		return None
	
	def valid_trace_name(self, name:str) -> bool:
		''' Ensures a trace has a valid name. Trace name rules, as taken from the ZVA manual:
		
		 - The first character of a trace name can be either one of the upper case letters A to Z, one 
		   of the lower case letters a to z, an underscore _ or a square bracket [ or ].
		 - For all other characters of a trace name, the numbers 0 to 9 can be used in addition.
		
		Args:
			name (str): Name to validate.
		
		Returns:
			bool: True if name is valid
		'''
		
		return True
	
	def _to_format_code(self, format:str) -> str:
		'''Takes a VNA category format constant and converts it
		to a format string understood by the VNA.
		
		Args:
			format (str): Accepts a format constant such as BasicVectorNetworkAnalyzerCtg.FORM_LOG_MAG
				and returns the equivalent format code understood by the hardware.
		
		Returns:
			str: format code understood by the hardware. Returns None if an error occurs.
		'''
		
		# Ensure key exists
		if format not in self.format_table.keys():
			self.error(f"Invalid format >{format}< provided.")
			return None
		
		# Return value
		return self.format_table[format]
	
	def _from_format_code(self, code:str) -> str:
		'''Takes a VNA category format constant and converts it
		to a format string by the category.
		
		Args:
			code (str): Format code as understood/provided by the hardware. 
		
		Returns:
			str: Format constant as understood by the class, such as BasicVectorNetworkAnalyzerCtg.FORM_LOG_MAG.
		'''
		
		# Find all keys that map to the given value
		matching_keys = [key for key, value in self.format_table.items() if value == code]
		
		# Check for errors
		if not matching_keys:
			self.debug(f"Format code >{code}< not found in format_table, setting to >:qFORM_OTHER<.")
			return BasicVectorNetworkAnalyzerCtg.FORM_OTHER
		elif len(matching_keys) > 1:
			self.warning(f"Multiple matches found for format code >{code}<. Matching keys: >{matching_keys}<. Selecting first match.")
		
		# Return matching key
		return matching_keys[0]
	
	def _to_meas_code(self, format:str) -> str:
		'''Takes a VNA category format constant and converts it
		to a format string understood by the VNA.
		
		Args:
			format (str): Accepts a format constant such as BasicVectorNetworkAnalyzerCtg.FORM_LOG_MAG
				and returns the equivalent format code understood by the hardware.
		
		Returns:
			str: format code understood by the hardware. Returns None if an error occurs.
		'''
		
		# Ensure key exists
		if format not in self.measurement_codes.keys():
			self.error(f"Invalid format >{format}< provided.")
			return None
		
		# Return value
		return self.measurement_codes[format]
	
	def _from_meas_code(self, code:str) -> str:
		'''Takes a VNA category format constant and converts it
		to a format string by the category.
		
		Args:
			code (str): Format code as understood/provided by the hardware. 
		
		Returns:
			str: Format constant as understood by the class, such as BasicVectorNetworkAnalyzerCtg.FORM_LOG_MAG.
		'''
		
		# Find all keys that map to the given value
		matching_keys = [key for key, value in self.measurement_codes.items() if value == code]
		
		# Check for errors
		if not matching_keys:
			self.debug(f"Measurement code >{code}< not found in measurement_codes, setting to >:qMEAS_S11<.")
			return BasicVectorNetworkAnalyzerCtg.MEAS_S11
		elif len(matching_keys) > 1:
			self.warning(f"Multiple matches found for meas. code >{code}<. Matching keys: >{matching_keys}<. Selecting first match.")
		
		# Return matching key
		return matching_keys[0]
		
	def refresh_channels_and_traces(self):
		''' Queries which channels are currently created. Created channels can be
		non-consecutive, so Ch1 and Ch3 could be active without Ch2.
		'''
		
		# Wipe channel and trace data - start over
		self.state.channels.clear()
		self.state.traces.clear()
		trc_idx = self.first_trace
		
		# Scan over all channels - determine which are enabled
		c_list = []
		for i in range(self.first_channel, self.first_channel+self.max_channels):
			
			#TODO: Store this info somewhere
			_enabled = False
			if str_to_bool(self.query(f"CONF:CHAN{i+1}:STAT?")):
				_enabled = True
				c_list.append(i+1)
				
				# Update channel state enabled tracker
				self.channels[i] = VNAChannelState()
				self.channels[i].enabled = True
			
			print(f"Read channel #{i} enabled:{_enabled}")
			

			# self.modify_state(None, ["channels", "enabled"], _enabled, indices=[i])
			#TODO: better to use modify_state or directly access state?
		
		# Read all channel settings and traces for all enabled channels
		for ch in c_list:
			
			# Get 'catalog' string describing traces and their measurements
			trace_state_str = self.query(f"CALC{ch}:PAR:CAT?")
			
			# Format trance string into something less arcane
			trace_state_str = trace_state_str[1:-2] # Trim quotes
			t_state_list = trace_state_str.strip().split(',') # Split at commas
			trace_data = {t_state_list[i]: t_state_list[i+1] for i in range(0, len(t_state_list)-1, 2)} # Break into dict of <trace-names>:<trace-measurements>
			
			# Loop over all traces for this channel...
			for trc_name, trc_meas in trace_data.items():
				
				# Create new trace
				self.state.traces[trc_idx] = VNATraceState()
				self.state.traces[trc_idx].enabled = True
				self.state.traces[trc_idx].channel = ch
				self.state.traces[trc_idx].id_str = trc_name
				self.state.traces[trc_idx].measurement = self._from_meas_code(trc_meas)
				
				# Select the specified measurement/trace
				self.write(f"CALC{ch}:PAR:SEL {trc_name}")
				
				# Get the trace format
				fmt_code = self.query(f"CALC{ch}:FORM?")
				self.state.traces[trc_idx].format = self._from_format_code(fmt_code)
				
				# Update trace iterator
				trc_idx += 1
			
			# Get channel settings
			self.get_freq_start(ch)
			self.get_freq_end(ch)
			self.get_res_bandwidth(ch)
			self.get_num_points(ch)
			self.get_power(ch)
	
	@superreturn
	def _set_active_trace(self, trace:int, channel:int=1):
		''' Sets the active trace on the display.'''
		self.write(f"CALC{channel}:PAR:SEL {self._to_trace_code(trace)}")
	
	@ superreturn
	def _get_active_trace(self, trace:int, channel:int=1):
		''' Sets the active trace on the display.'''
		self._super_hint = self._from_trace_code(self.query(f"CALC{channel}:PAR:SEL?"))
	
	@superreturn
	def set_freq_start(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STAR {f_Hz}")
	
	@superreturn
	def get_freq_start(self, channel:int=1):
		self._super_hint = float(self.query(f"SENS{channel}:FREQ:STAR?"))
	
	@superreturn
	def set_freq_end(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STOP {f_Hz}")
	
	@superreturn
	def get_freq_end(self, channel:int=1):
		self._super_hint = float(self.query(f"SENS{channel}:FREQ:STOP?"))
	
	@superreturn
	def set_power(self, p_dBm:float, channel:int=1, port:int=1):
		self.write(f"SOUR{channel}:POW{port}:LEV:IMM:AMPL {p_dBm}")
	
	@superreturn
	def get_power(self, channel:int=1, port:int=1):
		self._super_hint = float(self.query(f"SOUR{channel}:POW{port}:LEV:IMM:AMPL?"))
		# TODO: How to handle ports?
	
	@superreturn
	def set_num_points(self, points:int, channel:int=1):
		self.write(f"SENS{channel}:SWEEP:POIN {points}")
	
	@superreturn
	def get_num_points(self, channel:int=1):
		self._super_hint = int(self.query(f"SENS{channel}:SWEEP:POIN?") )
	
	@superreturn
	def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:BAND:RES {rbw_Hz}")
	
	@superreturn
	def get_res_bandwidth(self, channel:int=1):
		self._super_hint = float(self.query(f"SENS{channel}:BAND:RES?"))
	
	@superreturn
	def set_rf_enable(self, enable:bool):
		self.write(f"OUTP:STAT {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_rf_enable(self):
		self._super_hint = str_to_bool(self.query(f"OUTP:STAT?"))
	
	@superreturn
	def set_rf_power(self, power_dBm:float, channel:int=1):
		self.write(f"SOUR{channel}:POW {power_dBm}")
	
	@superreturn
	def get_rf_power(self, channel:int=1):
		self._super_hint = float(self.query(f"SOUR{channel}:POW?"))
	
	@superreturn
	def get_rf_power(self):
		self._super_hint = str_to_bool(self.query(f"OUTP:STAT?"))
	
	def clear_traces(self):
		self.write(f"CALC:PAR:DEL:ALL")
		
		#TODO: Update state
	
	def add_trace(self, channel:int, trace_name:str, measurement:str) -> bool:
		
		# Get measurement code
		try:
			meas_code = self.measurement_codes[measurement]
		except:
			self.log.error(f"Unrecognized measurement!")
			return False
		
		# Check that trace doesn't already exist
		exist_trace_names = [tr.id_str for tr in self.state.traces]
		if trace_name in exist_trace_names:
			self.error(f"Trace name >'{trace_name}'< already exists. Aborting add_trace.")
			return False
		
		if not self.valid_trace_name(trace_name):
			self.error(f"Trace name >'{trace_name}'< invalid. Aborting add_trace.")
			return False
		
		# Create measurement - will not display yet
		self.write(f"CALC{channel}:PAR:SDEF '{trace_name}', '{meas_code}'")
		
		# Create a trace and assoc. with measurement
		self.write(f"DISP:TRAC:EFE '{trace_name}'")
		
		#TODO: Update state
		
		return True
	
	def send_update_display(self):
		self.write(f"SYSTEM:DISPLAY:UPDATE ONCE")
	
	def get_trace_data(self, trace_name:str):
		'''
		
		Channel Data:
			* x: X data list, frequency (Hz) (float)
			* y: Y data list,  (float)
			* x_units: Units of x-axis
			* y_units: UNits of y-axis
		'''
		
		# Check that trace exists
		exist_trace_names = [tr.id_str for tr in self.state.traces]
		if trace_name not in exist_trace_names:
			self.log.error(f"Trace >'{trace_name}'< does not exist!")
			return
		
		# Get channel number for specified trace
		tr_idx = self._get_trace_idx(trace_name)
		channel = self.traces[tr_idx].channel
		
		# Select the specified measurement/trace
		self.write(f"CALC{channel}:PAR:SEL {trace_name}")
		
		# Set data format - 64-bit real numbers
		self.write(f"FORM:DATA REAL,64")
		
		# Request the trace data
		self.write(f"CALC{channel}:DATA? SDATA")
		
		# Read the packet header first (size prefix)
		header = self.inst.read_bytes(2)
		digits_in_size_num = int(header[1:2])
		
		# Read the size of the data packet
		size_bytes = self.inst.read_bytes(digits_in_size_num)
		packet_size = int(size_bytes.decode())
		
		# Read the actual packet data
		data_raw = self.inst.read_bytes(packet_size)
		
		# Convert the raw binary data to an array of floats
		float_data = list(array.array('d', data_raw))  # 'd' ensures double precision floats
		
		# Get frequency range
		f0 = self.get_freq_start()
		fe = self.get_freq_end()
		fnum = self.get_num_points()
		freqs_Hz = list(np.linspace(f0, fe, fnum))
		
		real_vals = float_data[0::2]  # Extract real components
		imag_vals = float_data[1::2]  # Extract imaginary components
		complex_trace = np.array(real_vals) + 1j * np.array(imag_vals)
		
		#TODO: Determine what type of trace is being measured and correct units
		#TODO: Handle non-s-parameter data correctly
		y_data = complex_trace
		y_unit = 'Reflection, complex, unitless'
		
		return {'x': freqs_Hz, 'y': y_data, 'x_units': 'Hz', 'y_units': y_unit}
	
	def refresh_data(self):
		pass
	
	# def get_channel_data(self, channel:int):
	# 	'''
		
	# 	Channel Data:
	# 		* x: X data list (float)
	# 		* y: Y data list (float)
	# 		* x_units: Units of x-axis
	# 		* y_units: UNits of y-axis
	# 	'''
		
	# 	self.log.warning(f"Binary transfer not implemented. Defaulting to slower ASCII.")
		
	# 	# # Check that trace exists
	# 	# if trace not in self.trace_lookup.keys():
	# 	# 	self.log.error(f"Trace number {trace} does not exist!")
	# 	# 	return
		
	# 	# trace_name = self.trace_lookup[trace]
		
	# 	# # Select the specified measurement/trace
	# 	# self.write(f"CALC{channel}:PAR:SEL {trace_name}")
		
	# 	# # Set data format
	# 	# self.write(f"FORM:DATA REAL,64")
		
	# 	self.write(f"CALCULATE{channel}:FORMAT REAL")
	# 	real_data = self.query(f"CALC{channel}:DATA? FDATA")
	# 	self.write(f"CALCULATE{channel}:FORMAT IMAG")
	# 	imag_data = self.query(f"CALC{channel}:DATA? FDATA")
	# 	real_tokens = real_data.split(",")
	# 	imag_tokens = imag_data.split(",")
	# 	trace = [complex(float(re), float(im)) for re, im in zip(real_tokens, imag_tokens)]
		
	# 	# Get frequency range
	# 	f0 = self.get_freq_start()
	# 	fe = self.get_freq_end()
	# 	fnum = self.get_num_points()
	# 	freqs_Hz = list(np.linspace(f0, fe, fnum))
		
	# 	return {'x': freqs_Hz, 'y': trace, 'x_units': 'Hz', 'y_units': 'Reflection (complex), unitless'}
		
		# # Query data
		# return self.query(f"CALC{channel}:DATA? SDATA")
		
	# def set_continuous_trigger(self, enable:bool):
	# 	self.write(f"INIT:CONT {bool_to_ONOFF(enable)}")
	# def get_continuous_trigger(self):
	# 	return str_to_bool(self.query(f"INIT:CONT?"))
	
	# def send_manual_trigger(self):
	# 	self.write(f"INIT:IMM")
		
	# def set_averaging_enable(self, enable:bool, channel:int=1):
	# 	self.write(f"SENS{channel}:AVER {bool_to_ONOFF(enable)}")
	# def get_averaging_enable(self, channel:int=1):
	# 	return str_to_bool(self.write(f"SENS{channel}:AVER?"))
	
	# def set_averaging_count(self, count:int, channel:int=1):
	# 	count = int(max(1, min(count, 65536)))
	# 	if count != count:
	# 		self.log.error(f"Did not apply command. Instrument limits values to integers 1-65536 and this range was violated.")
	# 		return
	# 	self.write(f"SENS{channel}:AVER:COUN {count}")
	# def get_averaging_count(self, channel:int=1):
	# 	return int(self.query(f"SENS{channel}:AVER:COUN?"))
	
	# def send_clear_averaging(self, channel:int=1):
	# 	self.write(f"SENS{channel}:AVER:CLE")
	
	# def send_preset(self):
	# 	self.write("SYST:PRES")