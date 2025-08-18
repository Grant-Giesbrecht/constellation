from constellation.base import *

class LockInAmplifierCtg(Driver):
	
	def __init__(self, address:str, log:plf.LogPile, expected_idn="", is_scpi:bool=False):
		super().__init__(address, log, expected_idn=expected_idn, is_scpi=is_scpi)
	
	@abstractmethod
	def set_offset(self, offset:float):
		pass