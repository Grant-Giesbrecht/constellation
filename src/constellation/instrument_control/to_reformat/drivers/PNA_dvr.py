''' Driver for PNA E8364B VNA

* Only supports a single window

Manual: https://www.testworld.com/wp-content/uploads/user-guide-help-agilent-e8362b-e8363b-e8364b-e8361a-n5230a-n5242a-pna-series-microwave-network-analyzers.pdf
'''

from constellation.base import *
from constellation.instrument_control.categories.vector_network_analyzer_ctg import *

class KeysightPNAE8364B(VectorNetworkAnalyzerCtg):
	
	SWEEP_CONTINUOUS = "sweep-continuous"
	SWEEP_SINGLE = "sweep-single"
	SWEEP_OFF = "sweep-off"
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log)
		
		self.trace_lookup = {}
		
		self.measurement_codes = {}
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S11] = "S11"
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S12] = "S12"
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S21] = "S21"
		self.measurement_codes[VectorNetworkAnalyzerCtg.MEAS_S22] = "S22"
	
	def set_freq_start(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STAR {f_Hz}")
	def get_freq_start(self, channel:int=1):
		return float(self.query(f"SENS{channel}:FREQ:STAR?"))
	
	def set_freq_end(self, f_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:FREQ:STOP {f_Hz}")
	def get_freq_end(self, channel:int=1):
		return float(self.query(f"SENS{channel}:FREQ:STOP?"))
	
	def set_power(self, p_dBm:float, channel:int=1, port:int=1):
		self.write(f"SOUR{channel}:POW{port}:LEV:IMM:AMPL {p_dBm}")
	def get_power(self, channel:int=1, port:int=1):
		return float(self.query(f"SOUR{channel}:POW{port}:LEV:IMM:AMPL?"))
	
	def set_num_points(self, points:int, channel:int=1):
		self.write(f"SENS{channel}:SWEEP:POIN {points}")
	def get_num_points(self, channel:int=1):
		return int(self.query(f"SENS{channel}:SWEEP:POIN?"))
	
	def set_res_bandwidth(self, rbw_Hz:float, channel:int=1):
		self.write(f"SENS{channel}:BAND:RES {rbw_Hz}")
	def get_res_bandwidth(self, channel:int=1):
		return float(self.query(f"SENS{channel}:BAND:RES?"))
	
	def set_rf_enable(self, enable:bool):
		self.write(f"OUTP:STAT {bool_to_ONOFF(enable)}")
	def get_rf_enable(self):
		return str_to_bool(self.query(f"OUTP:STAT?"))
	
	def clear_traces(self):
		self.write(f"CALC:PAR:DEL:ALL")
	
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
	
	def get_trace_data(self, channel:int, trace:int):
		
		# Check that trace exists
		if trace not in self.trace_lookup.keys():
			self.log.error(f"Trace number {trace} does not exist!")
			return
		
		trace_name = self.trace_lookup[trace]
		
		# Select the specified measurement/trace
		self.write(f"CALC{channel}:PAR:SEL {trace_name}")
		
		# Set data format
		self.write(f"FORM:DATA REAL,64")
		
		# Query data
		return self.query(f"CALC{channel}:DATA? SDATA")
		
	def set_continuous_trigger(self, enable:bool):
		self.write(f"INIT:CONT {bool_to_ONOFF(enable)}")
	def get_continuous_trigger(self):
		return str_to_bool(self.query(f"INIT:CONT?"))
	
	def send_manual_trigger(self):
		self.write(f"INIT:IMM")
		
	def set_averaging_enable(self, enable:bool, channel:int=1):
		self.write(f"SENS{channel}:AVER {bool_to_ONOFF(enable)}")
	def get_averaging_enable(self, channel:int=1):
		return str_to_bool(self.write(f"SENS{channel}:AVER?"))
	
	def set_averaging_count(self, count:int, channel:int=1):
		count = int(max(1, min(count, 65536)))
		if count != count:
			self.log.error(f"Did not apply command. Instrument limits values to integers 1-65536 and this range was violated.")
			return
		self.write(f"SENS{channel}:AVER:COUN {count}")
	def get_averaging_count(self, channel:int=1):
		return int(self.query(f"SENS{channel}:AVER:COUN?"))
	
	def send_clear_averaging(self, channel:int=1):
		self.write(f"SENS{channel}:AVER:CLE")
	
	def send_preset(self):
		self.write("SYST:PRES")