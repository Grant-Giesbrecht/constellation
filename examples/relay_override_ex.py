""" The purpose of this example is to show how custom Relay classes can be made
and passed to instrument drivers to modify their behavior. 
"""

from constellation.all import *
import matplotlib.pyplot as plt


class DummyConsoleRelay(CommandRelay):
	''' This is an example of how a different CommandRelay class can be made and passed to whichever insturment you need. Note, this class is purely illustrative and does NOT serve any practical purpose.
	
	Although it seems like a "dummy relay", note that it does not actually work like real dummy mode. Because it has no understanding of the instrument it relays for (and no relay is supposed to 
	understand the messages it transports), it cannot return valid data to the dummy 
	queries. Here it always returns `12` to every query and read call. This would error on some functions, for example, those expecting a bool. 
	'''
	
	def __init__(self):
		super().__init__()
		
		self.rm = pv.ResourceManager('@py')
		self.inst = None
	
	def connect(self) -> bool:
		
		print(f"Pretending to connect to {self.address}... -> ONLINE")
		return True
	
	def close(self) -> None:
		''' Attempts to close the connection to the physical 
		instrument.'''
		
		print(f"Closing connection")
	
	def write(self, cmd:str) -> bool:
		''' Sends a SCPI command via PyVISA.
		
		Args:
			cmd (str): Command to write to instrument.
		
		Returns:
			bool: Success status of write.
		'''
		
		print(f"WRITE: {cmd} -->")
		
		return True
	
	def read(self) -> tuple:
		''' Reads data as a string from the instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		print(f"READ <-- 12")
		
		return True, "12"
	
	def query(self, cmd:str) -> tuple:
		''' Queries data as a string from the instrument.
		
		Args:
			cmd (str): Command to query from instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		print(f"QUERY: {cmd} --> ")
		print(f"           <-- ")
		
		return True, "12"

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.LOWDEBUG

osc = RigolDS1000Z("TCPIP0::192.168.0.70::INSTR", relay=DummyConsoleRelay(), log=log, dummy=False)

osc.set_div_time(0.002)
osc.set_offset_time(0.005)
