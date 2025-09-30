from constellation.base import *
from constellation.networking.net_client import *

class ArbitraryWaveformGenerator(Driver):
	
	WAVE_SINE = "wave-sine"
	WAVE_SQUARE = "wave-sine"
	WAVE_RAMP = "wave-ramp"
	WAVE_PULSE = "wave-pulse"
	WAVE_NOISE = "wave-noise"
	WAVE_ARB = "wave-arb"
	WAVE_DC = "wave-dc"
	
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay, expected_idn:str="", dummy:bool=False, max_channels:int=2):
		super().__init__(address, log, expected_idn=expected_idn, dummy=dummy, relay=relay)
		
		self.max_channels = max_channels
	
	def apply_state(self, new_state: dict):
		pass
	
	def refresh_state(self):
		pass
		
	def refresh_data(self):
		pass