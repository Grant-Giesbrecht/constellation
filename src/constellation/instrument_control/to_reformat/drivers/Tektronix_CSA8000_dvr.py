''' Tektronix CSA8000 Driver


'''

import array
from constellation.base import *
from constellation.instrument_control.categories.spectrum_analyzer_ctg import *
import struct

# Max size of data packet to read
TK_CSA_DRIVER_MAX_READ_LEN = 1073741824

# TODO: Create CSA category
class TektronixCSA8000(Driver):
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, expected_idn="TEKTRONIX,CSA8")
	
	def get_waveform(self,channel:int=1): 
		'''  '''
		
		# Make sure trace is in range
		count = int(max(1, min(channel, 8)))
		if count != count:
			self.log.error(f"Did not apply command. Instrument limits values to integers 1-8 and this range was violated.")
			return
		
		# Set channel
		self.write(f"DATA:SOURCE CH{channel}")

		# Set mode to binary floating point
		self.write("DATA:ENC FPBINARY")

		# Read preamble

		# Read packet --------------------------------------
		# Packet starts with "":CURVE #<size><size2><DATA BLOCK ... >
		#
		#
		
		# Read data - ask for data
		self.write(f"CURVE?")
		data_raw = bytearray()
		
		# For this instrument, if I try to read in multiple commands it becomes
		# unstable. If I read the entire packet in one go, it works. This tries
		# to read 1 GB and aborts when a termination character is sent.
		byte = self.inst.read_bytes(TK_CSA_DRIVER_MAX_READ_LEN, break_on_termchar=True)
		data_raw += byte
		
		# Get size of size of packet block (ie. convert #4 -> (int)4 )
		digits_in_size_num = int(data_raw[8:9])
		print(f"digits in size num: {digits_in_size_num}")
		
		data_raw = byte
		packet_size = int(data_raw[9:9+digits_in_size_num])
		print(f"packet size: {packet_size}")
		
		self.log.debug(f"Binary fast waveform read: Expecting {packet_size} bytes for floats in packet.")
		
		# Instead of using array.array we have to use struct.unpack because the CSA sends
		# data in MSB order, which is not what array.array expects.
		#
		# Skip first X bytes (number of elements)
		num_points_f = packet_size/4
		num_points = round(num_points_f)
		if np.abs(num_points_f - num_points) > 0.01:
			self.log.error(f"Binary read is broken!")
		float_data = []
		for nidx in range(num_points):
			
			float_data.append(struct.unpack('>f', data_raw[9+digits_in_size_num+4*nidx:13+digits_in_size_num+4*nidx]))
		
		# Try to get bounds
		try:
			x_step = float(self.query("WFMO:XINCR?").split(" ")[-1])
			x_zero = float(self.query("WFMO:XZERO?").split(" ")[-1])
			y_zero = float(self.query("WFMO:YZERO?").split(" ")[-1])
		except Exception as e:
			self.log.error(f"Failed to get waveform data: interpreting waveform metadata failed.", detail=f"{e}")
			return None
		
		# Make time array
		time_s = np.linspace(x_zero, x_zero+(num_points-1)*x_step, num_points)
		
		out_data = {'x':time_s, 'y':float_data, 'x_units':'S', 'y_units':'Ohms'}
		
		return out_data
	
	# def set_freq_start(self, f_Hz:float):
	# 	self.write(f"SENS:FREQ:STAR {f_Hz} Hz")
	# def get_freq_start(self):
	# 	return float(self.query(f"SENS:FREQ:STAR?"))
	
	# def set_freq_end(self, f_Hz:float):
	# 	self.write(f"SENS:FREQ:STOP {f_Hz}")
	# def get_freq_end(self,):
	# 	return float(self.query(f"SENS:FREQ:STOP?"))
	
	# def set_ref_level(self, ref_dBm:float):
	# 	ref_dBm = max(-130, min(ref_dBm, 30))
	# 	if ref_dBm != ref_dBm:
	# 		self.log.error(f"Did not apply command. Instrument limits values from -130 to 30 dBm and this range was violated.")
	# 		return
	# 	self.write(f"CALC:UNIT:POW dBm") # Set units to DBM (Next command refers to this unit)
	# 	self.write(f"DISP:WIND:TRAC:Y:RLEV {ref_dBm}")
	# def get_ref_level(self):
	# 	return float(self.query("DISP:WIND:TRAC:Y:RLEV?"))
	
	# def set_y_div(self, step_dB:float):
		
	# 	step_dB = max(1, min(step_dB, 20))
	# 	if step_dB != step_dB:
	# 		self.log.error(f"Did not apply command. Instrument limits values from 1 to 20 dB and this range was violated.")
	# 		return
		
	# 	full_span_dB = step_dB*10 #Sets total span, not per div, so must multiply by num. divisions (10)
	# 	self.write(f":DISP:WIND:TRAC:Y:SCAL {full_span_dB} DB") 
	# def get_y_div(self):
	# 	full_span_dB = float(self.query(f":DISP:WIND:TRAC:Y:SCAL?"))
	# 	return full_span_dB/10
	
	# def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
	# 	self.write(f"SENS:BAND:RES {rbw_Hz} Hz")
	# def get_res_bandwidth(self, channel:int=1):
	# 	return float(self.query(f"SENS:BAND:RES?"))
	
	# def set_continuous_trigger(self, enable:bool):
	# 	self.write(f"INIT:CONT {bool_to_ONOFF(enable)}")
	# def get_continuous_trigger(self):
	# 	return str_to_bool(self.query(f"INIT:CONT?"))
	
	# def send_manual_trigger(self, send_cls:bool=True):
	# 	if send_cls:
	# 		self.write("*CLS")
	# 	self.write(f"INIT:IMM")
	
	# def get_trace_data(self, trace:int, use_ascii_transfer:bool=False, use_fast_binary:bool=True):
	# 	''' Returns the data of the trace in a standard waveform dict, which
		
	# 	has keys:
	# 		* x: X data list (float)
	# 		* y: Y data list (float)
	# 		* x_units: Units of x-axis
	# 		* y_units: UNits of y-axis
		
	# 	'''
		
	# 	# Make sure trace is in range
	# 	count = int(max(1, min(trace, 3)))
	# 	if count != count:
	# 		self.log.error(f"Did not apply command. Instrument limits values to integers 1-3 and this range was violated.")
	# 		return
		
	# 	# Get Y-unit
		
		
	# 	# Run ASCII transfer if requested
	# 	if use_ascii_transfer:
			
	# 		self.write(f"FORMAT:DATA ASCII") # Set format to ASCII
	# 		data_raw = self.query(f"TRACE:DATA? TRACE{trace}") # Get raw data
	# 		str_list = data_raw.split(",") # Split at each comma
	# 		del str_list[-1] # Remove last element (newline)
	# 		float_data = [float(x) for x in str_list] # Convert to float
			
	# 	else:
	# 		#  Example data would be:
	# 		#      #42500<data block of 2500 4 byte floats>
	# 		#	   THe '#4' indicates 4 bytes of data for size of packet
	# 		#      The 2500 indicates 2500 floats, or 2500*4 bytes
		
	# 		# Set data format - Real 32 binary data - in current Y unit
	# 		self.write(f"FORMAT:DATA REAL")
			
	# 		if not use_fast_binary:
			
	# 			# Read data - ask for data
	# 			self.write(f"TRACE:DATA? TRACE{trace}")
	# 			data_raw = bytearray()
	# 			while True:
	# 				try:
	# 					byte = self.inst.read_bytes(1)
	# 					data_raw += byte
	# 				except:
	# 					break
				
	# 			# Skip first 6 bytes (number of elements) and last byte (newline)
	# 			float_data = list(array.array('f', data_raw[6:-1]))
				
	# 		else:
				
	# 			# Read data - ask for data
	# 			self.write(f"TRACE:DATA? TRACE{trace}")
	# 			data_raw = bytearray()
				
	# 			# Get size of size of packet block (ie. convert #4 -> (int)4 )
	# 			byte = self.inst.read_bytes(2)
	# 			data_raw += byte
	# 			digits_in_size_num = int(data_raw[1:2])
				
	# 			# Read size of packet
	# 			byte = self.inst.read_bytes(digits_in_size_num)
	# 			data_raw += byte
	# 			packet_size = int(data_raw[2:2+digits_in_size_num])
				
	# 			#Read entire packet
	# 			byte = self.inst.read_bytes(packet_size+1)
	# 			data_raw += byte
				
	# 			# Skip first X bytes (number of elements) and last byte (newline)
	# 			float_data = list(array.array('f', data_raw[2+digits_in_size_num:-1]))
				
	# 	# Generate time array
	# 	f_list = list(np.linspace(self.get_freq_start(), self.get_freq_end(), len(float_data)))
		
	# 	out_data = {'x':f_list, 'y':float_data, 'x_units':'Hz', 'y_units':'dBm'}
		
	# 	# Convert Y-unit to dBm
	# 	return out_data
		
	# 	# trace_name = self.trace_lookup[trace]
		
	# 	# # Select the specified measurement/trace
	# 	# self.write(f"CALC{channel}:PAR:SEL {trace_name}")
		
	# 	# # Set data format
	# 	# self.write(f"FORM:DATA REAL,64")
		
	# 	# # Query data
	# 	# return self.query(f"CALC{channel}:DATA? SDATA")
		
	
		
	# # def set_averaging_enable(self, enable:bool, channel:int=1):
	# # 	self.write(f"SENS{channel}:AVER {bool_to_ONOFF(enable)}")
	# # def get_averaging_enable(self, channel:int=1):
	# # 	return str_to_bool(self.write(f"SENS{channel}:AVER?"))
	
	# # def set_averaging_count(self, count:int, channel:int=1):
	# # 	count = int(max(1, min(count, 65536)))
	# # 	if count != count:
	# # 		self.log.error(f"Did not apply command. Instrument limits values to integers 1-65536 and this range was violated.")
	# # 		return
	# # 	self.write(f"SENS{channel}:AVER:COUN {count}")
	# # def get_averaging_count(self, channel:int=1):
	# # 	return int(self.query(f"SENS{channel}:AVER:COUN?"))
	
	# # def send_clear_averaging(self, channel:int=1):
	# # 	self.write(f"SENS{channel}:AVER:CLE")
	
	# # def send_preset(self):
	# # 	self.write("SYST:PRES")