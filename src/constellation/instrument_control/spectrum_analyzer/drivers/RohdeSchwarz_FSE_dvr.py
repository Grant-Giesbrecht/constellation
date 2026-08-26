''' Driver for Rohde & Schwarz FSE series Spectrum Analyzers

* Only supports a single window (Referred to as Screen A in R&S documentation. The instruments supports screens A&B.)

Manual: https://scdn.rohde-schwarz.com/ur/pws/dl_downloads/dl_common_library/dl_manuals/gb_1/f/fsq_1/FSQ_OperatingManual_en_02.pdf
'''

import array
from constellation.base import *
from constellation.instrument_control.spectrum_analyzer.spectrum_analyzer_ctg import *

# Max size of data packet to read
RS_FSE_DRIVER_MAX_READ_LEN = 1073741824

class RohdeSchwarzFSE(SpectrumAnalyzer):
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, **kwargs):
		super().__init__(address, log, relay=relay, expected_idn="Rohde&Schwarz,FSE", **kwargs) # Example 'Rohde&Schwarz,FSQ-26,200334/026,4.75\n'
		
		self.trace_lookup = {}
	
	# NOTE: this driver previously called modify_state() itself with constants
	# (SpectrumAnalyzer.FREQ_START and friends) that do not exist on the category class - every
	# one of these methods raised AttributeError the moment it was called. It went unnoticed
	# because nothing ever called them: SpectrumAnalyzer.init_dummy_state() was an empty `pass`,
	# so constructing a dummy FSE touched none of this. Seeding dummy state surfaced it
	# immediately. Now migrated to the standard @superreturn pattern - the driver returns its
	# parsed value and the category handles all state tracking.
	
	@superreturn
	def set_freq_start(self, f_Hz:float):
		self.write(f"SENS:FREQ:STAR {f_Hz} Hz")
	
	@superreturn
	def get_freq_start(self):
		return float(self.query(f"SENS:FREQ:STAR?"))
	
	@superreturn
	def set_freq_end(self, f_Hz:float):
		self.write(f"SENS:FREQ:STOP {f_Hz}")
	
	@superreturn
	def get_freq_end(self):
		return float(self.query(f"SENS:FREQ:STOP?"))
	
	@superreturn
	def set_ref_level(self, ref_dBm:float):
		ref_dBm = max(-130, min(ref_dBm, 30))
		if ref_dBm != ref_dBm:
			self.log.error(f"Did not apply command. Instrument limits values from -130 to 30 dBm and this range was violated.")
			return
		self.write(f"CALC:UNIT:POW dBm") # Set units to DBM (Next command refers to this unit)
		self.write(f"DISP:WIND:TRAC:Y:RLEV {ref_dBm}")
	
	@superreturn
	def get_ref_level(self):
		return float(self.query("DISP:WIND:TRAC:Y:RLEV?"))
	
	@superreturn
	def set_y_div(self, step_dB:float):
		
		step_dB = max(1, min(step_dB, 20))
		if step_dB != step_dB:
			self.log.error(f"Did not apply command. Instrument limits values from 1 to 20 dB and this range was violated.")
			return
		
		full_span_dB = step_dB*10 #Sets total span, not per div, so must multiply by num. divisions (10)
		self.write(f":DISP:WIND:TRAC:Y:SCAL {full_span_dB} DB")
	
	@superreturn
	def get_y_div(self):
		full_span_dB = float(self.query(f":DISP:WIND:TRAC:Y:SCAL?"))
		return full_span_dB/10
	
	@superreturn
	def set_res_bandwidth(self, rbw_Hz:float):
		self.write(f"SENS:BAND:RES {rbw_Hz} Hz")
	
	@superreturn
	def get_res_bandwidth(self):
		return float(self.query(f"SENS:BAND:RES?"))
	
	@superreturn
	def set_continuous_trigger(self, enable:bool):
		self.write(f"INIT:CONT {bool_to_ONOFF(enable)}")
	
	@superreturn
	def get_continuous_trigger(self):
		return str_to_bool(self.query(f"INIT:CONT?"))
	
	def send_manual_trigger(self, send_cls:bool=True):
		if send_cls:
			self.write("*CLS")
		self.write(f"INIT:IMM")
	
	@superreturn
	def get_trace_data(self, trace:int, use_ascii_transfer:bool=False):
		''' Returns the data of the trace in a standard waveform dict, which
		
		has keys:
			* x: X data list (float)
			* y: Y data list (float)
			* x_units: Units of x-axis
			* y_units: Units of y-axis
		
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
			self.write(f"FORMAT:DATA REAL,32")
			
				
			# Read data - ask for data
			self.write(f"TRACE:DATA? TRACE{trace}")
			data_raw = bytearray()
			
			# For this instrument, if I try to read in multiple commands it becomes
			# unstable. If I read the entire packet in one go, it works. This tries
			# to read 1 GB and aborts when a termination character is sent.
			byte = self.relay.inst.read_bytes(RS_FSE_DRIVER_MAX_READ_LEN, break_on_termchar=True)
			data_raw += byte
			
			# Get size of size of packet block (ie. convert #4 -> (int)4 )
			digits_in_size_num = int(data_raw[1:2])

			# Read size of packet
			self.write(f"TRACE:DATA? TRACE{trace}") # Must send command - buffer seems to clear?

			data_raw = byte
			packet_size = int(data_raw[2:2+digits_in_size_num])
			
			self.log.debug(f"Binary fast waveform read: Expecting {packet_size} bytes for floats in packet.")
			
			# Skip first X bytes (number of elements) and last byte (newline)
			float_data = list(array.array('f', data_raw[2+digits_in_size_num:-1]))
				
		# Generate time array
		f_list = list(np.linspace(self.get_freq_start(), self.get_freq_end(), len(float_data)))
		
		out_data = {'x':f_list, 'y':float_data, 'x_units':'Hz', 'y_units':'dBm'}
		
		# State tracking is the category's job - @superreturn hands this return value up.
		return out_data
		
		# trace_name = self.trace_lookup[trace]
		
		# # Select the specified measurement/trace
		# self.write(f"CALC{channel}:PAR:SEL {trace_name}")
		
		# # Set data format
		# self.write(f"FORM:DATA REAL,64")
		
		# # Query data
		# return self.query(f"CALC{channel}:DATA? SDATA")
		