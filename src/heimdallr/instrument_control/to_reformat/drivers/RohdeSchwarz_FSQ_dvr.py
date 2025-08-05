''' Driver for Rohde & Schwarz FSQ series Spectrum Analyzers

* Only supports a single window (Referred to as Screen A in R&S documentation. The instruments supports screens A&B.)

Manual: https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/dl_common_library/dl_manuals/gb_1/f/fsq_1/FSQ_OperatingManual_en_02.pdf
'''

import array
from heimdallr.base import *
from heimdallr.instrument_control.categories.spectrum_analyzer_ctg import *

class RohdeSchwarzFSQ(SpectrumAnalyzerCtg):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, expected_idn="Rohde&Schwarz,FSQ-") # Example 'Rohde&Schwarz,FSQ-26,200334/026,4.75\n'
		
		self.trace_lookup = {}
	
	def set_freq_start(self, f_Hz:float):
		self.modify_state(self.get_freq_start, SpectrumAnalyzerCtg.FREQ_START, f_Hz)
		self.write(f"SENS:FREQ:STAR {f_Hz} Hz")
	def get_freq_start(self):
		return self.modify_state(None, SpectrumAnalyzerCtg.FREQ_START, float(self.query(f"SENS:FREQ:STAR?")))
	
	def set_freq_end(self, f_Hz:float):
		self.modify_state(self.get_freq_end, SpectrumAnalyzerCtg.FREQ_END, f_Hz)
		self.write(f"SENS:FREQ:STOP {f_Hz}")
	def get_freq_end(self,):
		return self.modify_state(None, SpectrumAnalyzerCtg.FREQ_END, float(self.query(f"SENS:FREQ:STOP?")))
	
	def set_ref_level(self, ref_dBm:float):
		ref_dBm = max(-130, min(ref_dBm, 30))
		if ref_dBm != ref_dBm:
			self.log.error(f"Did not apply command. Instrument limits values from -130 to 30 dBm and this range was violated.")
			return
		self.modify_state(self.get_ref_level, SpectrumAnalyzerCtg.REF_LEVEL, ref_dBm)
		self.write(f"CALC:UNIT:POW dBm") # Set units to DBM (Next command refers to this unit)
		self.write(f"DISP:WIND:TRAC:Y:RLEV {ref_dBm}")
	def get_ref_level(self):
		return self.modify_state(None, SpectrumAnalyzerCtg.REF_LEVEL, float(self.query("DISP:WIND:TRAC:Y:RLEV?")))
	
	def set_y_div(self, step_dB:float):
		
		step_dB = max(1, min(step_dB, 20))
		if step_dB != step_dB:
			self.log.error(f"Did not apply command. Instrument limits values from 1 to 20 dB and this range was violated.")
			return
		
		self.modify_state(self.get_y_div, SpectrumAnalyzerCtg.Y_DIV, step_dB)
		full_span_dB = step_dB*10 #Sets total span, not per div, so must multiply by num. divisions (10)
		self.write(f":DISP:WIND:TRAC:Y:SCAL {full_span_dB} DB") 
	def get_y_div(self):
		full_span_dB = float(self.query(f":DISP:WIND:TRAC:Y:SCAL?"))
		return self.modify_state(None, SpectrumAnalyzerCtg.Y_DIV, full_span_dB/10)
	
	def set_res_bandwidth(self, rbw_Hz:float):
		self.modify_state(self.get_res_bandwidth, SpectrumAnalyzerCtg.RES_BW, rbw_Hz)
		self.write(f"SENS:BAND:RES {rbw_Hz} Hz")
	def get_res_bandwidth(self):
		return self.modify_state(None, SpectrumAnalyzerCtg.RES_BW, float(self.query(f"SENS:BAND:RES?")))
	
	def set_continuous_trigger(self, enable:bool):
		self.modify_state(self.get_continuous_trigger, SpectrumAnalyzerCtg.CONTINUOUS_TRIG_EN, enable)
		self.write(f"INIT:CONT {bool_to_ONOFF(enable)}")
	def get_continuous_trigger(self):
		return self.modify_state(None, SpectrumAnalyzerCtg.CONTINUOUS_TRIG_EN, str_to_bool(self.query(f"INIT:CONT?")))
	
	def send_manual_trigger(self, send_cls:bool=True):
		if send_cls:
			self.write("*CLS")
		self.write(f"INIT:IMM")
	
	def get_trace_data(self, trace:int, use_ascii_transfer:bool=False, use_fast_binary:bool=True):
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
			
			self.write(f"FORMAT:DATA ASCII") # Set format to ASCII
			data_raw = self.query(f"TRACE:DATA? TRACE{trace}") # Get raw data
			str_list = data_raw.split(",") # Split at each comma
			del str_list[-1] # Remove last element (newline)
			float_data = [float(x) for x in str_list] # Convert to float
			
		else:
			#  Example data would be:
			#      #42500<data block of 2500 4 byte floats>
			#	   THe '#4' indicates 4 bytes of data for size of packet
			#      The 2500 indicates 2500 floats, or 2500*4 bytes
		
			# Set data format - Real 32 binary data - in current Y unit
			self.write(f"FORMAT:DATA REAL")
			
			if not use_fast_binary:
			
				# Read data - ask for data
				self.write(f"TRACE:DATA? TRACE{trace}")
				data_raw = bytearray()
				while True:
					try:
						byte = self.inst.read_bytes(1)
						data_raw += byte
					except:
						break
				
				# Skip first 6 bytes (number of elements) and last byte (newline)
				float_data = list(array.array('f', data_raw[6:-1]))
				
			else:
				
				# Read data - ask for data
				self.write(f"TRACE:DATA? TRACE{trace}")
				data_raw = bytearray()
				
				# Get size of size of packet block (ie. convert #4 -> (int)4 )
				byte = self.inst.read_bytes(2)
				data_raw += byte
				digits_in_size_num = int(data_raw[1:2])
				
				# Read size of packet
				byte = self.inst.read_bytes(digits_in_size_num)
				data_raw += byte
				packet_size = int(data_raw[2:2+digits_in_size_num])
				
				#Read entire packet
				byte = self.inst.read_bytes(packet_size+1)
				data_raw += byte
				
				# Skip first X bytes (number of elements) and last byte (newline)
				float_data = list(array.array('f', data_raw[2+digits_in_size_num:-1]))
				
		# Generate time array
		f_list = list(np.linspace(self.get_freq_start(), self.get_freq_end(), len(float_data)))
		
		out_data = {'x':f_list, 'y':float_data, 'x_units':'Hz', 'y_units':'dBm'}
		
		self.modify_state(None, SpectrumAnalyzerCtg.TRACE_DATA, out_data, channel=trace)
		
		# Convert Y-unit to dBm
		return out_data
		
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