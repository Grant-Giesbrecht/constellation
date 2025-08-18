import pyvisa as pv
import pylogfile.base as plf
from constellation.relay import *
import numpy as np
import time
import inspect
from abc import ABC, abstractmethod
from socket import getaddrinfo, gethostname
import ipaddress
import fnmatch
import matplotlib.pyplot as plt
from jarnsaxa import hdf_to_dict, dict_to_hdf, Packable
import datetime
import numbers


def get_ip(ip_addr_proto="ipv4", ignore_local_ips=True):
	# By default, this method only returns non-local IPv4 addresses
	# To return IPv6 only, call get_ip('ipv6')
	# To return both IPv4 and IPv6, call get_ip('both')
	# To return local IPs, call get_ip(None, False)
	# Can combine options like so get_ip('both', False)
	#
	# Thanks 'Geruta' from Stack Overflow: https://stackoverflow.com/questions/24196932/how-can-i-get-the-ip-address-from-a-nic-network-interface-controller-in-python

	af_inet = 2
	if ip_addr_proto == "ipv6":
		af_inet = 30
	elif ip_addr_proto == "both":
		af_inet = 0

	system_ip_list = getaddrinfo(gethostname(), None, af_inet, 1, 0)
	ip_list = []

	for ip in system_ip_list:
		ip = ip[4][0]

		try:
			ipaddress.ip_address(str(ip))
			ip_address_valid = True
		except ValueError:
			ip_address_valid = False
		else:
			if ipaddress.ip_address(ip).is_loopback and ignore_local_ips or ipaddress.ip_address(ip).is_link_local and ignore_local_ips:
				pass
			elif ip_address_valid:
				ip_list.append(ip)
	
	return ip_list

def wildcard(test:str, pattern:str):
	return len(fnmatch.filter([test], pattern)) > 0

def truncate_str(s:str, limit:int=14):
	''' Used in automatic logs to make sure a value converted to a string isn't super
	long. '''
	
	s = str(s)
	
	if len(s) <= limit:
		return s
	else:
		keep = (limit-3) // 2
		return s[:keep] + '...' + s[-keep - (1 if (limit-3)%2 else 0):]

def protect_str(x:str, limit:int=30):
	return "@:LOCK" + truncate_str(x, limit) + "@:UNLOCK"

class HostID:
	''' Contains the IP address and host-name for the host. Primarily used
	so drivers can quickly identify the host's IP address.'''
	
	def __init__(self, target_ips:str=["192.168.1.*", "192.168.*.*"]):
		''' Identifies the ipv4 address and host-name of the host.'''
		self.ip_address = ""
		self.host_name = ""
		
		# Get list of IP address for each network adapter
		ip_list = get_ip()
		
		# Scan over list and check each
		for target_ip in target_ips:
			for ipl in ip_list:
				
				# Check for match
				if wildcard(ipl, target_ip):
					self.ip_address = ipl
					break
		
		self.host_name = gethostname()
	
	def __str__(self):
		
		return f"ip-address: {self.ip_address}\nhost-name: {self.host_name}"

class Identifier:
	''' Data to identify a specific instrument driver instance. Contains
	its location on a network (if applicable), rich-name, class type, and
	identification string provided by the instrument.'''
	
	def __init__(self):
		self.idn_model = "" # Identifier provided by instrument itself (*IDN?)
		self.ctg = "" # Category class of driver
		self.dvr = "" # Driver class
		
		self.remote_id = "" # Rich name authenticated by the server and used to lookup the remote address
		self.remote_addr = "" # String IP address of driver host, pipe, then instrument VISA address.
		
		self.address = "" # Instrument address to connect to, if local connection.
	
	def to_dict(self):
		''' Returns the instrument Identifier as a dictionary.
		
		Returns:
			Dictionary representing the identifier.
		'''
		
		return {"idn_model":self.idn_model, "ctg":self.ctg, "dvr":self.dvr, "remote_id":self.remote_id, "remote_addr":self.remote_addr, "address":self.address}
	
	def short_str(self):
		dvr_short = self.dvr[self.dvr.rfind('.')+1:]
		if len(self.remote_id) > 0:
			return f"driver-class: {dvr_short}, remote-id: {self.remote_id}"
		else:
			return f"driver-class: {dvr_short}"
	
	def __str__(self):
		
		return f"idn_model: {self.idn_model}\ncategory: {self.ctg}\ndriver-class: {self.dvr}\nremote-id: {self.remote_id}\nremote-addr: {self.remote_addr}"

def superreturn(func):
	''' Calls a function's super after the overriding function finishes
	execution, passing identical arguments and returning the super's
	return value.'''
	
	def wrapper(self, *args, **kwargs):
		
		# Call the source function (but only if not in dummy mode)
		if not self.dummy:
			try:
				func(self, *args, **kwargs)
			except Exception as e:
				self.log.error(f"Failed to call driver function: >:a{func}< ({e}).")
				return None
		
		# Call super after, pass original arugments
		super_method = getattr(super(type(self), self), func.__name__)
		return super_method(*args, **kwargs)
	return wrapper

class InstrumentState(Packable):
	""" Used to describe the state of a Driver or instrument.
	"""
	
	def __init__(self, log:plf.LogPile):
		self.log = log
	
	def is_valid_type(self, test_obj):
		''' Checks if test_obj is a valid type for 
		'''
		if isinstance(test_obj, IndexedList):
			return True
		if isinstance(test_obj, Packable):
			return True
		if isinstance(test_obj, dict):
			return True
		if isinstance(test_obj, numbers.Number): # TODO: How to save complex to HDF/JSON/dict?
			return True
		if isinstance(test_obj, str):
			return True
		#TODO: Should lists be accepted?
		
		return False
	
	def set(self, params:tuple, value, indices:tuple=None) -> bool:
		''' Sets the value. Note that lists of objects MUST be stored
		in the IndexedList class.
		
		
		
		'''
		
		obj_under = None # Object one notch lower
		obj_top = self # Object at top of stack
		
		# Scan over all params... get top level object
		for idx, p in enumerate(params):
			
			# Check that parameter exists
			if not hasattr(obj_top, p):
				self.log.error(f"Cannot set state. Parameter >{p}< not found.", detail=f"params=({protect_str(params)}), indices=({protect_str(indices)}), value={protect_str(value)}")
				return False
			
			# Update object references
			obj_under = obj_top
			obj_top = getattr(obj_under, p)
			
			# Handle lists
			list_at_top = False # Indicates if the top level object is an IndexedList
			if isinstance(obj_top, IndexedList):
				
				# Validate that an index exists
				if indices is None:
					self.log.error(f"Cannot set state. Required a valid index tuple for indices paramter.")
					return False
				if len(indices) < idx+1:
					self.log.error(f"Cannot set state. Required indices paramter with greater length.")
					return False
				if indices[idx] is None:
					self.log.error(f"Cannot set state. Required indices paramter value not equal to None.")
					return False
				
				# Move into list if not at end of navigating tree
				if idx != len(params)-1:
					# Object is a list - shift obj_top to correct item in the list, not the list itself
					obj_top = obj_top.get_idx_val(indices[idx])
				else:
					list_at_top = True
		
		# Update value of final parameter
		if list_at_top:
			obj_top.set_idx_val(indices[idx], value)
		else:
			setattr(obj_under, p[-1], value)
		
		return True
	
	def get(self, params:tuple, indices:tuple=None):
		'''
		'''
		
		obj_under = None # Object one notch lower
		obj_top = self # Object at top of stack
		
		# Scan over all params... get top level object
		for idx, p in enumerate(params):
			
			# Check that parameter exists
			if not hasattr(obj_top, p):
				self.log.error(f"Cannot set state. Parameter >{p}< not found.", detail=f"params=({protect_str(params)}), indices=({protect_str(indices)}), value={protect_str(value)}")
				return None
			
			# Update object references
			obj_under = obj_top
			obj_top = getattr(obj_under, p)
			
			# Handle lists
			list_at_top = False # Indicates if the top level object is an IndexedList
			if isinstance(obj_top, IndexedList):
				
				# Validate that an index exists
				if indices is None:
					self.log.error(f"Cannot set state. Required a valid index tuple for indices paramter.")
					return None
				if len(indices) < idx+1:
					self.log.error(f"Cannot set state. Required indices paramter with greater length.")
					return None
				if indices[idx] is None:
					self.log.error(f"Cannot set state. Required indices paramter value not equal to None.")
					return None
				
				# Move into list if not at end of navigating tree
				if idx != len(params)-1:
					# Object is a list - shift obj_top to correct item in the list, not the list itself
					obj_top = obj_top.get_idx_val(indices[idx])
				else:
					list_at_top = True
		
		# Update value of final parameter
		if list_at_top:
			return obj_top.get_idx_val(indices[idx])
		else:
			return getattr(obj_under, p[-1])

class IndexedList:
	''' Used in driver.state and driver.data structures to organize values
	for parameters which apply to more than one index.
	
	It also supports 'traces' for instruments that have both multiple traces and 
	multiple indices such as a vector network analyzer.
	
	NOTE: Index numbering is internally zero-indexed. Most modern lab instruments
	with multiple indices use 1-based indexing. This discrepency, when handled by
	Heimdallr, will make the 1-based indexing purely cosmetic and converted to 0-based
	as soon as possible internally.
	'''
	
	#TODO: Add some validation to the value type. I think they need to be JSON-serializable.
	
	def __init__(self, first_index:int, num_indices:int, validate_type=None, log:plf.LogPile=None):
		
		self.first_index = first_index
		self.num_indices = num_indices
		self.index_data = {}
		
		self.validate_type = validate_type
		
		if log is None:
			self.log = plf.LogPile()
		else:
			self.log = log
	
	def summarize(self, indent:str=""):
		
		out = ""
		
		for ch in range(self.first_index, self.first_index+self.num_indices):
			if ch != self.first_index:
				out = out + "\n"
			val = self.get_idx_val(ch)
			out = out + f"{indent}>:qindex {ch}<: >:a@:LOCK{truncate_str(val, 40)}@:UNLOCK<@:LOCK, ({type(val)})@:UNLOCK"
		
		out = plf.markdown(out)
		return out
	
	def get_valid_idx(self, index:int) -> int:
		''' Checks if a given index number is valid. If not, returns
		closest valid index.
		
		Args:
			index (int): Index value to validate. Zero-indexed.
		
		Returns:
			int: Validated index number.
		
		'''
		if index >= self.first_index+self.num_indices:
			self.log.error(f"Max index exceeded. Defaulting to last possible index") #TODO: Needs prefix
			return self.first_index+self.num_indices-1
		elif index < self.first_index:
			self.log.error(f"Min index exceeded. Defaulting to first possible index") #TODO: Needs prefix
			return self.first_index
		else:
			return index
	
	def set_idx_val(self, index:int, value) -> None:
		''' Sets the value assigned to the specified index. 
		
		Args:
			index (int): Index number, zero-indexed.
			value (any): Value to assign to index.
		
		Returns:
			None
		'''
		
		if self.validate_type is not None:
			if not isinstance(value, self.validate_type):
				raise TypeError
		
		chan = self.get_valid_idx(index)
		self.index_data[chan] = value
	
	def get_idx_val(self, index:int):
		''' Get the value assigned to the index.
		
		Args:
			index (int): Index to get, zero-indexed.
		
		Returns:
			Value assigned to index. Any type. Returns None if value
			has not been assigned to index yet.
		'''
		chan = self.get_valid_idx(index)
		if not self.idx_is_populated(chan):
			self.log.error(f"Cannot return index value; index has not been populated.")
			return None
		return self.index_data[chan]
	
	def idx_is_populated(self, index:int):
		''' Checks if the specified index has been assigned a value.
		
		Args:
			index (int): Index to get, zero-indexed.
		
		Returns:
			bool: True if index has been assigned a value.
		'''
		
		return (index in self.index_data.keys())
	
	def to_dict(self):
		''' Converts the object to a dictionary so it can be saved
		more easily to disk.
		
		Returns:
			dict: Dictionary containing the value for each index,
			with index numbers (zero-indexed) as keys. For compatability
			with HDF, the keys as saved as "idx-<number>" rather than
			just the index number. Non-populated indices will not be saved
			to save space. Also includes two other keys, 'first_index' and 
			'num_indices' containing those state variables.
		'''
		
		data = {'first_index':self.first_index, 'num_indices':self.num_indices}
		for ch in range(self.first_index, self.first_index+self.num_indices):
			
			ch_str = f"idx-{ch}"
			
			if not self.idx_is_populated(ch):
				data[ch_str] = None
			else:
				data[ch_str] = self.get_idx_val(ch)
				
				#TODO: Error check that this item is JSON serializable
		
		return data
	
	def from_dict(self, data:dict):
		''' Populates the IndexedList from a dictionary.
		
		Args:
			data (dict): Dictionary to populate from. Expects a key 'first_index'
				and a key 'num_indices' to define the valid index range. All 
				other keys follow the format 'ch-<n>' where n is the index number,
				and the following value is the data value, in any JSON serializable
				format.
		
		Returns:
			bool: True if dictionary was properly interpreted.
		'''
		
		try:
			self.first_index = data['first_index']
			self.num_indices = data['num_indices']
		except Exception as e:
			self.log.error(f"Failed to populate IndexedList from dict ({e}).")
			return False
		
		# Scan over all items
		for k_str, v in data.items():
			
			if k_str == 'first_index' or k_str == 'num_indices':
				continue
			
			# Attempt to get int key and set value
			try:
				# Get just the index number
				ch = int(k_str.split('-', 1)[1])
				
				# Save value
				self.set_idx_val(ch, v)
				
			except Exception as e:
				self.log.error(f"Failed to parse key(>:q@:LOCK{k_str}@:UNLOCK<), value(>:q@:LOCK{v}@:UNLOCK<) pair. ({e})")
				return False
		
		return True

class DataEntry:
	''' Used in driver.data to describe a measurement result and its
	accompanying time.'''
	
	def __init__(self):
		self.update_time = None
		self.value = []
		
		#TODO: Idea was to have a hash of the data so I can tell if something
		# has been changed and needs to be updated, mostly in the context of having 
		# multiple instrument clients in a network environment (and comparing hashes with the relay to
		# know when to update over the network). However, this is complicated and I'm not sure it's really
		# worth while. 
		self.data_hash = None

class Driver(ABC):
	
	#TODO: Modify all category and drivers to pass kwargs to super
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay, expected_idn:str="", is_scpi:bool=True, remote_id:str=None, host_id:HostID=None, client_id:str="", dummy:bool=False, first_channel_num:int=1, first_trace_num:int=1):
		
		self.address = address
		self.log = log
		self.is_scpi = is_scpi
		self.hid = host_id
		
		self.id = Identifier()
		self.expected_idn = expected_idn
		self.verified_hardware = False
		
		#TODO: Will be replaced by Relay
		self.online = False
		self.relay = relay
		
		# Configure relay with address and log
		self.relay.configure(self.address, self.log)
		
		# State tracking parameters
		self.dummy = False
		self.blind_state_update = False
		self.state = {}
		self.data = {} # Each value is a DataEntry instance
		self.state_change_log_level = plf.DEBUG
		self.data_state_change_log_level = plf.DEBUG
		self._super_hint = None # Last measured value 
		
		# Setup ID
		self.id.remote_addr = client_id + "|" + self.address
		if remote_id is not None:
			self.id.remote_id = remote_id
			
		# Get category
		inheritance_list = inspect.getmro(self.__class__)
		dvr_o = inheritance_list[0]
		ctg_o = inheritance_list[1]
		self.id.ctg = f"{ctg_o}"
		self.id.dvr = f"{dvr_o}"
		self.id.address = self.address
		
		# Dummy variables
		self.dummy = dummy
		
		# These parameters are used for certain instruments, but need to be
		# defined in the Driver class so state saving/loading can see them.
		self.first_channel = first_channel_num
		self.max_channels = None
		self.first_trace = first_trace_num
		self.max_traces = None
		
		
		#TODO: Automatically reconnect
		# Connect instrument
		self.connect()
	
	def connect(self, check_id:bool=True) -> bool:
		''' Attempts to establish a connection to the instrument. Updates
		the self.online parameter with connection success.
		
		Args:
			check_id (bool): Check that instrument identifies itself as
				the expected model. Default is true. 
			
		Returns:
			bool: Online status
		'''
		
		# Return immediately if dummy mode
		if self.dummy:
			self.online = True
			return True
		
		# Tell the relay to attempt to reconnect
		if not self.relay.connect():
			self.error(f"Failed to connect to address: {self.address}.", detail=f"{self.id}")
			self.online = False
			return False
		self.online = True
		
		# Test if relay was successful in connecting
		if check_id:
			self.query_id()
		
		if self.online:
			self.debug(f"Connected to address >{self.address}<.", detail=f"{self.id}")
		else:
			self.error(f"Failed to connect to address: {self.address}. ({e})", detail=f"{self.id}")
		
		return self.online
	
	def preset(self) -> None:
		''' Presets an instrument. Only valid for SCPI instruments.'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default preset() function, instrument does recognize SCPI commands.", detail=f"{self.id}")
			return
		
		self.debug(f"Preset.", detail=f"{self.id}")
		
		self.write("*RST")
	
	def query_id(self) -> None:
		''' Checks the IDN of the instrument, and makes sure it matches up
		with the expected identified for the given instrument model. Updates
		self.online if connection/verification fails.
		
		Returns:
			None
		'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default query_id() function, instrument does recognize SCPI commands.", detail=f"{self.id}")
			return
		
		# Query IDN model
		self.id.idn_model = self.query("*IDN?").strip()
		
		if self.id.idn_model is not None:
			self.online = True
			self.debug(f"Connection state: >ONLINE<")
			
			if self.expected_idn is None or self.expected_idn == "":
				self.debug("Cannot verify hardware. No verification string provided.")
				return
			
			# Check if model is right
			if self.expected_idn.upper() in self.id.idn_model.upper():
				self.verified_hardware = True
				self.debug(f"Hardware verification >PASSED<", detail=f"Received string: {self.id.idn_model}")
			else:
				self.verified_hardware = False
				self.debug(f"Hardware verification >FAILED<", detail=f"Received string: {self.id.idn_model}")
		else:
			self.debug(f"Connection state: >OFFLINE<")
			self.online = False
		
	def close(self) -> None:
		''' Attempts to close the connection from the relay to the physical
		instrument. '''
		
		self.relay.close()
	
	def wait_ready(self, check_period:float=0.1, timeout_s:float=None):
		''' Waits until all previous SCPI commands have completed. *CLS 
		must have been sent prior to the commands in question.
		
		Set timeout to None for no timeout.
		
		Returns true if operation completed, returns False if timeout occured.'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default wait_ready() function, instrument does recognize SCPI commands.")
			return
		
		self.write(f"*OPC")
		
		# Check ESR
		esr_buffer = int(self.query(f"*ESR?"))
		
		t0 = time.time()
		
		# Loop while ESR bit one is not set
		while esr_buffer == 0:
			
			# Check register state
			esr_buffer = int(self.query(f"*ESR?"))
			
			# Wait prescribed time
			time.sleep(check_period)
			
			# Timeout handling
			if (timeout_s is not None) and (time.time() - t0 >= timeout_s):
				break
		
		# Return
		if esr_buffer > 0:
			return True
		else:
			return False
		
	def write(self, cmd:str) -> None:
		''' Sends a SCPI command via the drivers Relay. Updates
		self.online with write success/fail.
		
		Args:
			cmd (str): Command to relay to instrument
		
		Returns:
			None
		'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default write() function, instrument does recognize SCPI commands.")
			return
		
		# Abort if offline
		if not self.online:
			self.warning(f"Cannot write when offline.")
			return
		
		# Spoof if dummy
		if self.dummy:
			self.lowdebug(f"Writing to dummy: >@:LOCK{cmd}@:UNLOCK<.") # Put the SCPI command within a Lock - otherwise it can confuse the markdown
			return
		
		# Attempt write
		try:
			self.online = self.relay.write(cmd)
			if self.online:
				self.lowdebug(f"Wrote to instrument: >{cmd}<.")
		except Exception as e:
			self.error(f"Failed to write to instrument {self.address}. ({e})")
			self.online = False
	
	def read(self) -> str:
		''' Reads via the relay. Updates self.online with read success/
		failure.
		
		Returns:
			str: Value received from instrument relay.
		'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default read() function, instrument does recognize SCPI commands.")
			return ""
		
		# Abort if offline
		if not self.online:
			self.warning(f"Cannot write when offline. ()")
			return ""
		
		# Spoof if dummy
		if self.dummy:
			self.lowdebug(f"Reading from dummy")
			return ""
		
		# Attempt to read
		try:
			self.online, rv = self.relay.read()
			if self.online:
				self.lowdebug(f"Read from instrument: >:a{rv}<")
				return rv
			else:
				return ""
		except Exception as e:
			self.error(f"Failed to read from instrument {self.address}. ({e})")
			self.online = False
			return ""
	
	def query(self, cmd:str) -> str:
		''' Queries via the relay. Updates self.online with read success/
		failure.
		
		Args:
			cmd (str): Command to query from instrument.
		
		Returns:
			str: Value received from instrument relay.
		'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default read() function, instrument does recognize SCPI commands.")
			return ""
		
		# Abort if offline
		if not self.online:
			self.warning(f"Cannot query when offline. ()")
			return ""
		
		# Spoof if dummy
		if self.dummy:
			self.lowdebug(f"Reading from dummy")
			return ""
		
		# Attempt to read
		try:
			self.online, rv = self.relay.query(cmd)
			if self.online:
				self.lowdebug(f"Read from instrument: >:a{rv}<")
				return rv
			else:
				return ""
		except Exception as e:
			self.error(f"Failed to read from instrument {self.address}. ({e})")
			self.online = False
			return ""
	
	def dummy_responder(self, func_name:str, *args, **kwargs):
		''' Function expected to behave as the "real" equivalents. ie. write commands don't
		need to return anything, reads commands or similar should. What is returned here
		should mimic what would be returned by the "real" function if it were connected to
		hardware.
		'''
		
		# Put everything in a try-catch in case arguments are missing or similar
		try:
			
			# Respond to dummy function
			if "set_" == func_name[:4]:
				self.debug(f"Default dummy responder sending >None< to set_ function (>{func_name}<).")
				return None
			elif "get_" == func_name[:4]:
				self.debug(f"Default dummy responder sending >-1< to get_ function (>{func_name}<).")
				return -1
			else:
				self.debug(f"Default dummy responder sending >None< to unrecognized function (>{func_name}<).")
				return None
		except Exception as e:
			self.error(f"Failed to respond to dummy instruction. ({e})")
			return None
	
	def lowdebug(self, message:str, detail:str=""):
		self.log.lowdebug(f"(>:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def debug(self, message:str, detail:str=""):
		self.log.debug(f"(>:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def info(self, message:str, detail:str=""):
		self.log.info(f"(>:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def warning(self, message:str, detail:str=""):
		self.log.warning(f"(>:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def error(self, message:str, detail:str=""):
		self.log.error(f"(>:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
		
	def critical(self, message:str, detail:str=""):
		self.log.critical(f"(>:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def modify_state(self, query_func:callable, params:tuple, value, indices:tuple=None):
		"""
		Updates the internal state tracker.
		
		Parameters:
			query_func (callable): Function used to query the state of this parameter from
				the instrument. This parameter should be set to None if modify_state is 
				being called from a query function. 
			param (tuple): Tuple of strings containing the state class attribute(s) to 
				update. Multiple parameters can be passed for nested objects.
			value: Value for parameter being sent to the instrument. This will be used to
				update the internal state if query_func is None, or if the instrument is in
				dummy mode or blind_state_update mode. 
			indices (tuple): Tuple of ints. If N strings are contained in the `param`
				tuple, indices must contain N-1 ints. indices's first value contains the index for 
				the first param, assuming it's an IndexedList. If it is not, pass None for that
				value in indices.
			
		Returns:
			value, or result of query_func if provided.
		"""
		
		if (query_func is None) or self.dummy or self.blind_state_update:
			# For these cases, the instrument is not queried (or at least, not again). Instead,
			# the `value` parameter is saved to the interal state tracker and returned.
			
			prev_val = self.state.get(params, indices=indices)
			
			# Record ing log
			self.log.add_log(self.state_change_log_level, f"(>:q{self.id.short_str()}<) State modified: >{params}<=>:a{truncate_str(value)}<.", detail=f"Previous value was {truncate_str(prev_val)}")
			
			self.state.set(params, value, indices=indices)
			
			# if channel is None:
			# 	self.state[param] = value
			# else:
			# 	try:
			# 		self.state[param].set_idx_val(channel, value)
			# 	except Exception as e:
			# 		self.log.error(f"Failed to modify internal state. {e}")
			val = value
		else:
			val = query_func()
		
		return val
	
	def modify_data_state(self, query_func:callable, param:str, value, channel:int=None):
		"""
		Updates the internal data-state tracker.
		
		Parameters:
			query_func (callable): Function used to query the state of this parameter from
				the instrument. This parameter should be set to None if modify_state is 
				being called from a query function. 
			param (str): Parameter to update
			value: Value for parameter being sent to the instrument. This will be used to
				update the internal state if query_func is None, or if the instrument is in
				dummy mode or blind_state_update mode. 
			channel (int): Optional value for parameters that apply to individual channels of
				an instrument. Should be set to None (default) for parameters which do not
				have multiple channels. Channels are indexed from 1, not 0.
			
		Returns:
			value, or result of query_func if provided.
		"""
		
		if (query_func is None) or self.dummy or self.blind_state_update:
			prev_val = self.data[param]
			
			# Record ing log
			self.log.add_log(self.state_change_log_level, f"(Driver: >:q{self.id.short_str()}<) State modified; >{param}<=>:a{truncate_str(value)}<.", detail=f"Previous value was {truncate_str(prev_val)}")
			
			if channel is None:
				self.data[param] = value
			else:
				try:
					self.data[param].set_idx_val(1, value)
				except Exception as e:
					self.error(f"Failed to modify internal state. {e}")
			val = value
		else:
			val = query_func()
		
		return val
	
	def print_state(self):
		
		def mdprint(s:str):
			print(plf.markdown(s))
		
		def split_param(s):
			before_brackets = s[:s.index("[")]
			inside_brackets = s[s.index("[")+1:s.index("]")]
			return before_brackets, inside_brackets
		
		for k, v in self.state.items():
			
			# Get name and unit strings
			name, unit = split_param(k)
			
			# Print value
			if isinstance(v, IndexedList):
				mdprint(f">:q{name}<:")
				mdprint(f"    value:")
				print(v.summarize(indent="        "))
				mdprint(f"    unit: >{unit}<")
			else:
				mdprint(f">:q{name}<:")
				mdprint(f"    value: >:a{truncate_str(v, limit=40)}<")
				mdprint(f"    unit: >{unit}<")
	
	def state_to_dict(self, include_data:bool=False):
		''' Saves the current instrument state to a dictionary. Note that it does NOT
		refresh the state from the actual hardware. That must be done seperately
		using `refresh_state()`.
		
		Args:
			include_data (bool): Optional argument to include instrument data state
				as well. Default = False.
		
		Returns:
			dict: Dictionary representing state
		'''
		
		# Create metadata dict
		meta_dict = {}
		meta_dict["timestamp"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')
		meta_dict["instrument_id"] = self.id.to_dict()
		meta_dict["dummy"] = self.dummy
		meta_dict["is_scpi"] = self.is_scpi
		meta_dict["verified_hardware"] = self.verified_hardware
		meta_dict["online"] = self.online
		meta_dict["blind_state_update"] = self.blind_state_update
		meta_dict["max_channels"] = self.max_channels
		meta_dict["max_traces"] = self.max_traces
		
		# Create state dictionary
		state_dict = {}
		for k, v in self.state.items():
			
			if isinstance(v, IndexedList):
				state_dict[k] = v.to_dict()
			else:
				state_dict[k] = v
		
		# CreaTe data dictionary if requested, package output dict
		if include_data:
			
			data_dict = {}
			for k, v in self.data.items():
				
				if isinstance(v, IndexedList):
					data_dict[k] = v.to_dict()
				else:
					data_dict[k] = v
			
			out_dict = {"metadata":meta_dict, "state":state_dict, "data":data_dict}
		else:
			out_dict = {"metadata":meta_dict, "state":state_dict}
		
		return out_dict
	
	def save_state(self, filename:str, include_data:bool=False):
		''' Saves the current instrument state to disk. Note that it does NOT
		refresh the state from the actual hardware. That must be done seperately
		using `refresh_state()`.
		
		Args:
			filename (str): File to save.
			include_data (bool): Optional argument to include instrument data state
				as well. Default = False.
		
		Returns:
			bool: True if successfully saved file.
		'''
		
		#TODO: Also make JSON option
		
		# Generate dictionary
		out_dict = self.state_to_dict(include_data=include_data)
		
		# Save data
		return dict_to_hdf(out_dict, filename)
	
	def load_state_dict(self, state_dict:dict):
		''' Loads a state from a dictionary. Note that this only updates the 
		internal state, it does NOT apply the state to the hardware. To do this,
		the `apply_state()` function must be used.
		
		Args:
			state_dict (dict): State dictionary to apply to the internal state. 
		
		Returns:
			bool: True if state is succesfully loaded.
		'''
		
		# Get max channels and traces
		try:
			self.max_channels = int(state_dict['metadata']['max_channels'])
		except:
			self.max_channels = None
		
		try:
			self.max_traces = int(state_dict['metadata']['max_traces'])
		except:
			self.max_traces = None
		
		# Interpret state parameters
		try:
			
			# Get state dictionary
			sd = state_dict['state']
			
			# Loop over dictionary
			for k, v in sd.items():
				
				# Check if value is a dictionary
				if isinstance(v, dict):
					self.state[k].from_dict(v) # Dictionaries are from IndexedList objects
				else:
					self.state[k] = v # All others are directly saved
			
		except Exception as e:
			self.error(f"Failed to apply state dictionary ({e}).")
			return False
		
		return True
	
	def load_state(self, filename:str):
		''' Loads a state from file. Note that this only updates the 
		internal state, it does NOT apply the state to the hardware. To do this,
		the `apply_state()` function must be used.
		
		Args:
			filename (str): State file to read. Should be HDF format.
		
		Returns:
			bool: True if state is succesfully loaded.
		'''
		
		#TODO: Also accept JSON
		
		# Read file
		in_dict = hdf_to_dict(filename)
		
		# Apply to state
		return self.load_state_dict(in_dict)
	
	@abstractmethod
	def refresh_state(self):
		"""
		Calls all 'get' functions to fully update the state tracker.
		"""
		pass
	
	@abstractmethod
	def apply_state(self, new_state:dict):
		"""
		Applys a state (same format at self.state) to the instrument.
		"""
		pass
	
	@abstractmethod
	def refresh_data(self):
		"""
		Calls all 'get' functions to fully update the data tracker.
		"""
		pass
	
def bool_to_str01(val:bool):
	''' Converts a boolean value to 0/1 as a string '''
	
	if val:
		return "1"
	else:
		return "0"

def bool_to_ONOFF(val:bool):
	''' Converts a boolean value to 0/1 as a string '''
	
	if val:
		return "ON"
	else:
		return "OFF"

def str_to_bool(val:str):
	''' Converts the string 0/1 or ON/OFF or TRUE/FALSE to a boolean '''
	
	if ('1' in val) or ('ON' in val.upper()) or ('TRUE' in val.upper()):
		return True
	else:
		return False

def s2hms(seconds):
	''' Converts a value in seconds to a tuple of hours, minutes, seconds.'''
	
	# Convert seconds to minutes
	min = np.floor(seconds/60)
	seconds -= min*60
	
	# Convert minutes to hours
	hours = np.floor(min/60)
	min -= hours*60
	
	return (hours, min, seconds)

def plot_spectrum(spectrum:dict, marker='.', linestyle=':', color=(0, 0, 0.7), autoshow=True):
	''' Plots a spectrum dictionary, as returned by the Spectrum Analyzer drivers.
	
	Expects keys:
		* x: X data list (float)
		* y: Y data list (float)
		* x_units: Units of x-axis
		* y_units: Units of y-axis
	
	
	'''
	
	x_val = spectrum['x']
	x_unit = spectrum['x_units']
	if spectrum['x_units'] == "Hz":
		x_unit = "Frequency (GHz)"
		x_val = np.array(spectrum['x'])/1e9
	
	y_unit = spectrum['y_units']
	if y_unit == "dBm":
		y_unit = "Power (dBm)"
	
	plt.plot(x_val, spectrum['y'], marker=marker, linestyle=linestyle, color=color)
	plt.xlabel(x_unit)
	plt.ylabel(y_unit)
	plt.grid(True)
	
	if autoshow:
		plt.show()

def interpret_range(rd:dict, print_err=False):
	''' Accepts a dictionary defining a sweep list/range, and returns a list of the values. Returns none
	if the format is invalid.
	
	* Dictionary must contain key 'type' specifying the string 'list' or 'range'.
	* Dictionary must contain a key 'unit' specifying a string with the unit.
	* If type=list, dictionary must contain key 'values' with a list of each value to include.
	* If type=range, dictionary must contain keys start, end, and step each with a float value
	  specifying the iteration conditions for the list. Can include optional parameter 'delta'
	  which accepts a list of floats. For each value in the primary range definition, it will
	  also include values relative to the original value by each delta value. For example, if
	  the range specifies 10 to 20 in steps of one, and deltas = [-.1, 0.05], the final resulting
	  list will be 10, 10.05, 10.9, 11, 11.05, 11.9, 12, 12.05... and so on.
	
	Example list dict (in JSON format):
		 {
			"type": "list",
			"unit": "dBm",
			"values": [0]
		}
		
	Example range dict (in JSON format):
		{
			"type": "range",
			"unit": "Hz",
			"start": 9.8e9,
			"step": 1e6,
			"end": 10.2e9
		}
	
	Example range dict (in JSON format): Deltas parameter will add points at each step 100 KHz below each point and 10 KHz above to check derivative.
		{
			"type": "range",
			"unit": "Hz",
			"start": 9.8e9,
			"step": 1e6,
			"end": 10.2e9,
			"deltas": [-100e3, 10e3]
		}
	
	'''
	K = rd.keys()
	
	# Verify type parameter
	if "type" not in K:
		if print_err:
			print(f"    {Fore.RED}Key 'type' not present.{Style.RESET_ALL}")
		return None
	elif type(rd['type']) != str:
			if print_err:
				print(f"    {Fore.RED}Key 'type' wrong type.{Style.RESET_ALL}")
			return None
	elif rd['type'] not in ("list", "range"):
		if print_err:
			print(f"    {Fore.RED}Key 'type' corrupt.{Style.RESET_ALL}")
		return None
	
	# Verify unit parameter
	if "unit" not in K:
		if print_err:
			print(f"    {Fore.RED}Key 'unit' not present.{Style.RESET_ALL}")
		return None
	elif type(rd['unit']) != str:
		if print_err:
			print(f"    {Fore.RED}Key 'unit' wrong type.{Style.RESET_ALL}")
		return None
	elif rd['unit'] not in ("dBm", "V", "Hz", "mA", "K"):
		if print_err:
			print(f"    {Fore.RED}Key 'unit' corrupt.{Style.RESET_ALL}")
		return None
	
	# Read list type
	if rd['type'] == 'list':
		try:
			vals = rd['values']
		except:
			if print_err:
				print(f"    {Fore.RED}Failed to read value list.{Style.RESET_ALL}")
			return None
	elif rd['type'] == 'range':
		try:
			
			start = int(rd['start']*1e6)
			end = int(rd['end']*1e6)+1
			step = int(rd['step']*1e6)
			
			vals = np.array(range(start, end, step))/1e6
			
			vals = list(vals)
			
			# Check if delta parameter is defined
			if 'deltas' in rd.keys():
				deltas = rd['deltas']
				
				# Add delta values
				new_vals = []
				for v in vals:
					
					new_vals.append(v)
					
					# Apply each delta
					for dv in deltas:
						# print(v+dv)
						if (v+dv >= rd['start']) and (v+dv <= rd['end']):
							# print("  -->")
							new_vals.append(v+dv)
						# else:
						# 	print("  -X")
					
				# Check for an remove duplicates - assign to vals
				vals = list(set(new_vals))
				vals.sort()
			
		except Exception as e:
			if print_err:
				print(f"    {Fore.RED}Failed to process sweep values. ({e}){Style.RESET_ALL}")
			return None
	
	return vals

def enabledummy(func):
	'''Decorator to allow functions to trigger their parent Category's
	dummy_responder() function, with the name of the triggering function
	and the passed arguments.'''
	
	def wrapper(self, *args, **kwargs):
		
		# If in dummy mode, activate the dummy_responder instead of attempting to interact with hardware
		if self.dummy:
			return self.dummy_responder(func.__name__, *args, **kwargs)
			
		# Call the source function (this should just be 'pass')
		return func(self, *args, **kwargs)

	return wrapper