import pylogfile.base as plf
from abc import abstractmethod
from pyvicp import Client
import pyvisa as pv

class CommandRelay:
	''' Class used to relay commands from a "driver" (which defines the content of the
	instructions in commands) to the physical instrument. Using a CommandRelay object
	allows the Driver to relay commands directly to a instrument via Pyvisa, or to
	use a remote connection, with the difference being entirely invisible to the 
	driver.
	'''
	
	def __init__(self):
		
		self.address = ""
		self.log = None
	
	def configure(self, address:str, log:plf.LogPile):
		''' Configures the Relay with the appropriate address and log. Note
		that this is done after __init__ so that the Relay can be automatically
		configured by the driver in the driver's __init__ function, without the user
		having to change the address for both the relay and the driver.
		'''
		
		self.address = address
		self.log = log
	
	@abstractmethod
	def connect(self):
		''' Instructs relay to attempt to open the connection with the instrument,
		though note that this function will not be able to positively confirm a 
		successful connection.
		
		Returns:
			bool: False if connection was known to fail, else true.
		'''
		pass
	
	@abstractmethod
	def close(self):
		pass
	
	@abstractmethod
	def write(self):
		pass
	
	@abstractmethod
	def read(self):
		pass
	
	@abstractmethod
	def query(self):
		pass

class VICPDirectSCPIRelay(CommandRelay):
	''' A relay that directly connects to instruments via VICP and relays
	SCPI commands from a driver. This is only for LeCroy oscilloscopes because
	they require VICP instead of PyVisa.
	'''
	
	def __init__(self):
		super().__init__()
		
		self.instr = None
	
	def connect(self) -> bool:
		
		try:
			self.inst = Client(self.address)
			self.online = True
			self.log.debug(f"VICPDirectSCPIRelay attempting to open instrument at address >{self.address}<.")
		except:
			self.log.debug(f"VICPDirectSCPIRelay failed to open instrument at address >{self.address}<.")
			return False 
		return True
	
	def close(self) -> None:
		''' Attempts to close the connection to the physical 
		instrument.'''
		
		self.inst.close()
	
	def write(self, cmd:str) -> bool:
		''' Sends a SCPI command via PyVISA.
		
		Args:
			cmd (str): Command to write to instrument.
		
		Returns:
			bool: Success status of write.
		'''
		
		try:
			self.inst.send(cmd.encode())
			self.log.lowdebug(f"VICPDirectSCPIRelay wrote to instrument: >@:LOCK{cmd}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"VICPDirectSCPIRelay failed to write to instrument {self.address}. ({e})")
			return False
		
		return True
	
	def read(self) -> tuple:
		''' Reads data as a string from the instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.receive().decode()
			self.log.lowdebug(f"VICPDirectSCPIRelay read from instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"VICPDirectSCPIRelay failed to write to instrument {self.address}. ({e})")
			return False, ""
		
		return True, ""
	
	def query(self, cmd:str) -> tuple:
		''' Queries data as a string from the instrument.
		
		Args:
			cmd (str): Command to query from instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			self.inst.send(cmd.encode())
			rv = self.inst.receive().decode()
			self.log.lowdebug(f"DirectSCPIRelay queried from instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to query instrument {self.address}. ({e})")
			return False, ""
		
		return True, ""
	
class DirectSCPIRelay(CommandRelay):
	''' A relay that directly connects to instruments via PyVisa and relays
	SCPI commands from a driver.
	'''
	
	def __init__(self):
		super().__init__()
		
		self.rm = pv.ResourceManager('@py')
		self.inst = None
	
	def connect(self) -> bool:
		
		try:
			self.inst = self.rm.open_resource(self.address)
			self.online = True
			self.log.debug(f"DirectSCPIRelay attempting to open instrument at address >{self.address}<.")
		except:
			self.log.debug(f"DirectSCPIRelay failed to open instrument at address >{self.address}<.")
			return False 
		return True
	
	def close(self) -> None:
		''' Attempts to close the connection to the physical 
		instrument.'''
		
		self.inst.close()	
	
	def write(self, cmd:str) -> bool:
		''' Sends a SCPI command via PyVISA.
		
		Args:
			cmd (str): Command to write to instrument.
		
		Returns:
			bool: Success status of write.
		'''
		
		try:
			self.inst.write(cmd)
			self.log.lowdebug(f"DirectSCPIRelay wrote to instrument: >@:LOCK{cmd}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to write to instrument {self.address}. ({e})")
			return False
		
		return True
	
	def read(self) -> tuple:
		''' Reads data as a string from the instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.read()
			self.log.lowdebug(f"DirectSCPIRelay read from instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to read from instrument {self.address}. ({e})")
			return False, ""
		
		return True, ""
	
	def query(self, cmd:str) -> tuple:
		''' Queries data as a string from the instrument.
		
		Args:
			cmd (str): Command to query from instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.query(cmd)
			self.log.lowdebug(f"DirectSCPIRelay queried instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to query instrument {self.address}. ({e})")
			return False, ""
		
		return True, rv

class RemoteTextCommandRelayClient(CommandRelay):
	''' A relay that connects to an instrument via a network and relays
	SCPI commands indirectly from a driver.
	'''
	
	def __init__(self):
		super().__init__()
		
		self.rm = pv.ResourceManager('@py')
		self.inst = None
	
	def connect(self) -> bool:
		
		try:
			self.inst = self.rm.open_resource(self.address)
			self.online = True
			self.log.debug(f"DirectSCPIRelay attempting to open instrument at address >{self.address}<.")
		except:
			self.log.debug(f"DirectSCPIRelay failed to open instrument at address >{self.address}<.")
			return False 
		return True
	
	def close(self) -> None:
		''' Attempts to close the connection to the physical 
		instrument.'''
		
		self.inst.close()	
	
	def write(self, cmd:str) -> bool:
		''' Sends a SCPI command via PyVISA.
		
		Args:
			cmd (str): Command to write to instrument.
		
		Returns:
			bool: Success status of write.
		'''
		
		try:
			self.inst.write(cmd)
			self.log.lowdebug(f"DirectSCPIRelay wrote to instrument: >@:LOCK{cmd}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to write to instrument {self.address}. ({e})")
			return False
		
		return True
	
	def read(self) -> tuple:
		''' Reads data as a string from the instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.read()
			self.log.lowdebug(f"DirectSCPIRelay read from instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to read from instrument {self.address}. ({e})")
			return False, ""
		
		return True, ""
	
	def query(self, cmd:str) -> tuple:
		''' Queries data as a string from the instrument.
		
		Args:
			cmd (str): Command to query from instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.query(cmd)
			self.log.lowdebug(f"DirectSCPIRelay queried instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to query instrument {self.address}. ({e})")
			return False, ""
		
		return True, rv
	
	def __init__(self):
		pass

class RemoteTextCommandRelayListener:
	
	def __init__(self):
		pass