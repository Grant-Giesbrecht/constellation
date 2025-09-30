''' Driver for Siglent SSA3000X Spectrum Analyzers

* Only supports a single window

Manual: https://www.testworld.com/wp-content/uploads/user-guide-help-agilent-e8362b-e8363b-e8364b-e8361a-n5230a-n5242a-pna-series-microwave-network-analyzers.pdf
	or
	    https://siglentna.com/wp-content/uploads/dlm_uploads/2017/10/SSA3000X_ProgrammingGuide_PG0703X_E04A.pdf
'''

import array
from constellation.base import *
from constellation.instrument_control.spectrum_analyzer.spectrum_analyzer_ctg import *

class SiglentSSA3000X(SpectrumAnalyzer):
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=DirectSCPIRelay(), **kwargs):
		super().__init__(address, log, relay=relay, expected_idn="Siglent Technologies,SSA30", **kwargs)
		
		self.trace_lookup = {}
	
	@superreturn
	def set_freq_start(self, f_Hz:float):
		self.write(f"SENS:FREQ:STAR {f_Hz} Hz")
	
	@superreturn
	def get_freq_start(self):
		self._super_hint = float(self.query(f"SENS:FREQ:STAR?"))
	
	@superreturn
	def set_freq_end(self, f_Hz:float):
		self.write(f"SENS:FREQ:STOP {f_Hz}")
	
	@superreturn
	def get_freq_end(self, points:int):
		self._super_hint = float(self.query(f"SENS:FREQ:STOP?"))
	
	# @superreturn
	# def get_num_points(self, channel: int = 1):
	# 	pass
	
	# @superreturn
	# def get_num_points(self, channel: int = 1):
	# 	pass
	
	@superreturn
	def set_ref_level(self, ref_dBm:float):
		ref_dBm = max(-100, min(ref_dBm, 30))
		if ref_dBm != ref_dBm:
			self.log.error(f"Did not apply command. Instrument limits values from -100 to 30 dBm and this range was violated.")
			return
		
		self.write(f"DISP:WIND:TRAC:Y:RLEV {ref_dBm} DBM")
	
	@superreturn
	def get_ref_level(self):
		self._super_hint = float(self.query("DISP:WIND:TRAC:Y:RLEV?"))
	
	@superreturn
	def set_y_div(self, step_dB:float):
		step_dB = max(1, min(step_dB, 20))
		if step_dB != step_dB:
			self.log.error(f"Did not apply command. Instrument limits values from 1 to 20 dB and this range was violated.")
			return
		
		self.write(f":DISP:WIND:TRAC:Y:SCAL:PDIV {step_dB} DB")
	
	@superreturn
	def get_y_div(self):
		self._super_hint = float(self.query(f":DISP:WIND:TRAC:Y:SCAL:PDIV?"))
	
	@superreturn
	def set_res_bandwidth(self, rbw_Hz:float):
		self.write(f"SENS:BWID:RES {rbw_Hz}")
	
	@superreturn
	def get_res_bandwidth(self):
		self._super_hint =  float(self.query(f"SENS:BWID:RES?"))
	
	@superreturn
	def set_continuous_trigger(self, enable:bool):
		self.write(f"INIT:CONT {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_continuous_trigger(self):
		self._super_hint = str_to_bool(self.query(f"INIT:CONT?"))
	
	@superreturn
	def send_manual_trigger(self):
		self.write(f"INIT:IMM")
	
	@superreturn
	def get_trace_data(self, trace:int, use_ascii_transfer:bool=False):
		''' Returns the data of the trace in a standard waveform dict, which
		
		has keys:
			* x: X data list (float)
			* y: Y data list (float)
			* x_units: Units of x-axis
			* y_units: UNits of y-axis
		
		'''
		
		# Make sure trace is in range
		count = int(max(1, min(trace, 3)))
		if count != count:
			self.log.error(f"Did not apply command. Instrument limits values to integers 1-3 and this range was violated.")
			return
		
		# Get Y-unit
		
		
		# Run ASCII transfer if requested
		if use_ascii_transfer:
			
			self.write(f"FORMAT:TRACE:DATA ASCII") # Set format to ASCII
			data_raw = self.query(f"TRACE:DATA? {trace}") # Get raw data
			str_list = data_raw.split(",") # Split at each comma
			del str_list[-1] # Remove last element (newline)
			float_data = [float(x) for x in str_list] # Convert to float
			
		else:
		
			# Set data format - Real 64 binary data - in current Y unit
			self.write(f"FORMAT:TRACE:DATA REAL")
			
			# Read data - ask for data
			self.write(f"TRACE:DATA? {trace}")
			data_raw = []
			while True:
				try:
					byte = self.inst.read_bytes(1)
					data_raw.append(byte)
				except:
					break
			data_raw = data_raw[0]
			
			# Skip first 4 bytes (number of elements) and last byte (newline)
			float_data = list(array.array('f', data_raw[4:-1]))
		
		# Generate time array
		f_list = list(np.linspace(self.get_freq_start(), self.get_freq_end(), len(float_data)))
		
		self._super_hint = {'x':f_list, 'y':float_data, 'x_units':'Hz', 'y_units':'dBm'}
		
		# Convert Y-unit to dBm
		
		
		# trace_name = self.trace_lookup[trace]
		
		# # Select the specified measurement/trace
		# self.write(f"CALC{channel}:PAR:SEL {trace_name}")
		
		# # Set data format
		# self.write(f"FORM:DATA REAL,64")
		
		# # Query data
		# return self.query(f"CALC{channel}:DATA? SDATA")
		
	
		
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