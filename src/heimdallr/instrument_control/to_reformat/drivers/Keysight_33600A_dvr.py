''' Driver for Keysight 33xxx series of arbitrary waveform generators

Primarily from translating MATLAB code originally written by Manuel Castellanos-Beltran 
'''

import array
from heimdallr.base import *
# from heimdallr.instrument_control.categories.spectrum_analyzer_ctg import *

class Keysight_33000X(Driver):
	
	SOURCE_INTERNAL = "INT"
	SORUCE_EXTERNAL = "EXT"
	
	FILTER_NORMAL = "NORM"
	FILTER_OFF = "OFF"
	FILTER_STEP = "STEP"
	
	def __init__(self, address:str, log:plf.LogPile):
		super().__init__(address, log, expected_idn="Agilent Technologies,33")
		
		self.trace_lookup = {}
	
	def setBurstState(self, channel:int, state:bool):
		
		self.write(f"OUTP{channel} 0")
		self.write(f"SOUR{channel}:BURS:STAT {bool_to_str01(state)}")
	
	def setBurstNCycles(self, channel:int, num_cycles:int):
		self.write(f"SOUR{channel}:BURS:NCYC {num_cycles}")
	
	def setAmpPp(self, channel:int, amplitude:float):
		print(f"SOUR{channel}:VOLT {amplitude}")
		self.write(f"SOUR{channel}:VOLT {amplitude}")
	
	def setArbSampleRate(self, channel:int, sample_rate_Hz:float):
		self.write(f"SOUR{channel}:FUNC:ARB:SRAT {sample_rate_Hz}")
	
	def setTriggerSource(self, channel:int, trig_source:str):
		
		source_str = None
		if trig_source.upper() == "BUS":
			source_str = "BUS"
		elif trig_source.upper() == "IMMEDIATE":
			source_str = "IMM"
		elif trig_source.upper() == "EXT":
			source_str = "EXT"
		else:
			return False
		
		self.write(f"TRIG{channel}:SOUR {source_str}")
		
		return True
		
	def setTriggerLevel(self, channel:int, level:float):
		self.write(f"TRIG{channel}:LEV {level}")
	
	def setTriggerDelay(self, channel:int, delay:float):
		self.write(f"TRIG{channel}:DEL {delay}")
	
	def setSourceState(self, channel:int, state:bool):
		self.write(f"OUTP{channel} {bool_to_str01(state)}")
	
	def set10MHzSource(self, source:str):
		
		if source != Keysight_33000X.SOURCE_INTERNAL and source != Keysight_33000X.SORUCE_EXTERNAL:
			return False
		
		self.write(f"ROSC:SOUR {source}")
		
		return True
	
	def download_arb_data_bin(self, channel:int, arb_data:list, arb_name:str):
		
		# Record amplitude and sampel rate
		vpp = self.query(f"SOUR{channel}:VOLT?")
		srate = self.query(f"SOUR{channel}:FUNC:ARB:SRAT?")
		self.write(f"SOUR{channel}:DATA:VOL:CLE")
		
		self.log.info(f"vpp = {vpp}, srate = {srate}")
		
		ad_csv = ""
		for ad in arb_data:
			ad_csv = ad_csv + f"{ad}, "
		ad_csv = ad_csv[:-2]
		self.write(f"SOUR{channel}:DATA:ARB:DAC {arb_name}, {ad_csv}")
		time.sleep(1)
		print(ad_csv)
		
			
		# # self.write(arb_data)
		self.write(f"SOUR{channel}:FUNC:ARB {arb_name}")
		# self.write(f"SOUR{channel}:FUNC ARB")
		
		# self.setAmpPp(channel, vpp)
		# self.setArbSampleRate(channel, srate)
	
	def set_filter(self, channel:int, filter_code:str):
		
		if filter_code not in [Keysight_33000X.FILTER_OFF, Keysight_33000X.FILTER_NORMAL, Keysight_33000X.FILTER_STEP]:
			return False
		
		self.write(f"SOUR{channel}:FUNC:ARB:FILT {filter_code}")
		
		return True
	
	def print_error(self, num_errors:int=-1):
		
		if num_errors < 1:
			
			while True:
				estr = self.query(f"SYST:ERR?")
				if estr[:3] == "+0,":
					break
				else:
					print(f"ERROR: {estr}")
			
		else:
		
			for i in range(num_errors):
				estr = self.query(f"SYST:ERR?")
				print(f"ERROR: {estr}")