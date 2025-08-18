''' Driver for Rhode & Schwarz ZVA

Manual (Requires login and R&S approval): https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/dl_common_library/dl_manuals/gb_1/z/zva_2/ZVA_ZVB_ZVT_OperatingManual_en_33.pdf
'''

from heimdallr.base import *
from heimdallr.instrument_control.categories.vector_network_analyzer_ctg import *
import array

class RohdeSchwarzZVA(VectorNetworkAnalyzerCtg):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, expected_idn="Rohde&Schwarz,ZVA")
		
		# This translates the string measurement codes defined the the VectorNetworkAnalyzerCtg class
		# to strings that are understood by the specific instrument model (the ZVA).
		self.measurement_codes = {}
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S11] = "S11"
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S12] = "S12"
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S21] = "S21"
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S22] = "S22"
	
	def _to_trace_code(self, trace:int):
		''' Converts a trace number to the string format 
		understood by the ZVA'''
		return f"Trc{trace}"
	
	def set_freq_start(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STAR {f_Hz}")
		self.modify_state(self.get_freq_start, VectorNetworkAnalyzerCtg.FREQ_START, f_Hz, channel=channel)
	def get_freq_start(self, channel:int=1):
		return self.modify_state(None, VectorNetworkAnalyzerCtg.FREQ_START, float(self.query(f"SENS{channel}:FREQ:STAR?")), channel=channel)
	
	def set_freq_end(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STOP {f_Hz}")
		self.modify_state(self.get_freq_end, VectorNetworkAnalyzerCtg.FREQ_END, f_Hz, channel=channel)
	def get_freq_end(self, channel:int=1):
		return self.modify_state(None, VectorNetworkAnalyzerCtg.FREQ_END, float(self.query(f"SENS{channel}:FREQ:STOP?")), channel=channel)
	
	def set_power(self, p_dBm:float, channel:int=1, port:int=1):
		self.write(f"SOUR{channel}:POW{port}:LEV:IMM:AMPL {p_dBm}")
		self.modify_state(self.get_power, VectorNetworkAnalyzerCtg.POWER, p_dBm, channel=channel) #TODO: HOw to handle ports?
	def get_power(self, channel:int=1, port:int=1):
		return self.modify_state(None, VectorNetworkAnalyzerCtg.POWER, float(self.query(f"SOUR{channel}:POW{port}:LEV:IMM:AMPL?")), channel=channel)
		# TODO: How to handle ports?
	
	def set_num_points(self, points:int, channel:int=1):
		self.write(f"SENS{channel}:SWEEP:POIN {points}")
		self.modify_state(self.get_num_points, VectorNetworkAnalyzerCtg.NUM_POINTS, points, channel=channel)
	def get_num_points(self, channel:int=1):
		return self.modify_state(None, VectorNetworkAnalyzerCtg.NUM_POINTS, int(self.query(f"SENS{channel}:SWEEP:POIN?")), channel=channel)
	
	def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:BAND:RES {rbw_Hz}")
		self.modify_state(self.get_res_bandwidth, VectorNetworkAnalyzerCtg.RES_BW, rbw_Hz, channel=channel)
	def get_res_bandwidth(self, channel:int=1):
		return self.modify_state(None, VectorNetworkAnalyzerCtg.RES_BW, float(self.query(f"SENS{channel}:BAND:RES?")), channel=channel)
	
	def set_rf_enable(self, enable:bool):
		self.write(f"OUTP:STAT {bool_to_ONOFF(enable)}")
		self.modify_state(self.get_rf_enable, VectorNetworkAnalyzerCtg.ENABLE, enable)
	def get_rf_enable(self):
		return self.modify_state(None, VectorNetworkAnalyzerCtg.ENABLE, str_to_bool(self.query(f"OUTP:STAT?")))
	
	def clear_traces(self):
		self.write(f"CALC:PAR:DEL:ALL")
	
	def get_traces(self):
		trace_state_str = self.query("CALC:PAR:CAT?")
		
		# Reformat string into list of trace names, then measurements
		trace_state_list = (trace_state_str.strip()[1:-1]).split(',')
		
		# Verify even number
		if len(trace_state_list) % 2 != 0:
			self.error(f"Received invalid trace state string. Had odd length.")
			return False
		
		# Break into names and measurements
		trace_names = trace_state_list[0::2]
		trace_meas = trace_state_list[1::2]
		
		# Check that all names start with Trc
		for tn in trace_names:
			
			try:
				check_str = tn[:3]
			except:
				self.error(f"Received invalid trace state string. Too-short trace name.")
				return False
			
			if check_str != "Trc":
				self.error(f"Received invalid trace state string. Too-short trace name.")
				return False
		
		# Get each measurement type
		# <TODO>
	
	def add_trace(self, channel:int, trace:int, measurement:str):
		
		# Get measurement code
		try:
			meas_code = self.measurement_codes[measurement]
		except:
			self.log.error(f"Unrecognized measurement!")
			return
		
		# Check that trace doesn't already exist
		if trace in self.trace_lookup.keys():
			self.log.error(f"Cannot add trace. Trace number {trace} already exists.")
		
		# Create name and save
		trace_name = f"trace{trace}"
		self.trace_lookup[trace] = trace_name
		
		# Create measurement - will not display yet
		self.write(f"CALC{channel}:PAR:DEF '{trace_name}', {meas_code}")
		
		# Create a trace and assoc. with measurement
		self.write(f"DISP:WIND:TRAC{trace}:FEED '{trace_name}'")
	
	# def get_trace
	
	def send_update_display(self):
		self.write(f"SYSTEM:DISPLAY:UPDATE ONCE")
	
	def get_trace_data(self, channel:int, trace:int):
		'''
		
		Channel Data:
			* x: X data list, frequency (Hz) (float)
			* y: Y data list,  (float)
			* x_units: Units of x-axis
			* y_units: UNits of y-axis
		'''
		
		# # Check that trace exists
		# if trace not in self.trace_lookup.keys():
		# 	self.log.error(f"Trace number {trace} does not exist!")
		# 	return
		
		# trace_name = self.trace_lookup[trace]
		trace_name = self._to_trace_code(trace)
		
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
		
		# print(f"packet size = {packet_size}")
		
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
		y_data = complex_trace
		y_unit = 'Reflection, complex, unitless'
		
		return {'x': freqs_Hz, 'y': y_data, 'x_units': 'Hz', 'y_units': y_unit}
	
	def get_channel_data(self, channel:int):
		'''
		
		Channel Data:
			* x: X data list (float)
			* y: Y data list (float)
			* x_units: Units of x-axis
			* y_units: UNits of y-axis
		'''
		
		self.log.warning(f"Binary transfer not implemented. Defaulting to slower ASCII.")
		
		# # Check that trace exists
		# if trace not in self.trace_lookup.keys():
		# 	self.log.error(f"Trace number {trace} does not exist!")
		# 	return
		
		# trace_name = self.trace_lookup[trace]
		
		# # Select the specified measurement/trace
		# self.write(f"CALC{channel}:PAR:SEL {trace_name}")
		
		# # Set data format
		# self.write(f"FORM:DATA REAL,64")
		
		self.write(f"CALCULATE{channel}:FORMAT REAL")
		real_data = self.query(f"CALC{channel}:DATA? FDATA")
		self.write(f"CALCULATE{channel}:FORMAT IMAG")
		imag_data = self.query(f"CALC{channel}:DATA? FDATA")
		real_tokens = real_data.split(",")
		imag_tokens = imag_data.split(",")
		trace = [complex(float(re), float(im)) for re, im in zip(real_tokens, imag_tokens)]
		
		# Get frequency range
		f0 = self.get_freq_start()
		fe = self.get_freq_end()
		fnum = self.get_num_points()
		freqs_Hz = list(np.linspace(f0, fe, fnum))
		
		return {'x': freqs_Hz, 'y': trace, 'x_units': 'Hz', 'y_units': 'Reflection (complex), unitless'}
		
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