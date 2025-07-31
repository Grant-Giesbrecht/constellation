import pyvisa as pv
import pylogfile.base as plf
import numpy as np
import time
import inspect
from abc import ABC, abstractmethod
from socket import getaddrinfo, gethostname
import ipaddress
import fnmatch
import matplotlib.pyplot as plt

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
	
	def short_str(self):
		
		return f"(driver-class: {self.dvr}, remote-id: {self.remote_id})"
	
	def __str__(self):
		
		return f"idn_model: {self.idn_model}\ncategory: {self.ctg}\ndriver-class: {self.dvr}\nremote-id: {self.remote_id}\nremote-addr: {self.remote_addr}"

def superreturn(func):
	''' Calls a function's super after the overriding function finishes
	execution, passing identical arguments and returning the super's
	return value.'''
	
	def wrapper(self, *args, **kwargs):
		# Call the source function
		func(self, *args, **kwargs)
		# Call super after, pass original arugments
		return self.super(*args, **kwargs)
	return wrapper

class ChannelList:
	''' Used in driver.state and driver.data structures to organize values
	for parameters which apply to more than one channel.
	
	NOTE: Channel numbering is internally zero-indexed. Most modern lab instruments
	with multiple channels use 1-based indexing. This discrepency, when handled by
	Heimdallr, will make the 1-based indexing purely cosmetic and converted to 0-based
	as soon as possible internally.
	'''
	
	#TODO: Add some validation to the value type. I think they need to be JSON-serializable.
	
	def __init__(self, max_channels:int, log:plf.LogPile=None):
		
		self.max_channels = max_channels
		self.channel_data = {}
		
		if log is None:
			self.log = plf.LogPile()
		else:
			self.log = log
	
	def get_valid_ch(self, channel:int) -> int:
		''' Checks if a given channel number is valid. If not, returns
		closest valid channel.
		
		Args:
			channel (int): Channel value to validate. Zero-indexed.
		
		Returns:
			int: Validated channel number.
		
		'''
		if channel >= self.max_channels:
			self.log.error(f"Max channel count exceeded. Defaulting to last possible channel") #TODO: Needs prefix
			return self.max_channels-1
		elif channel < 0:
			return 0
		else:
			return channel
	
	def set_ch_val(self, channel:int, value) -> None:
		''' Sets the value assigned to the specified channel. 
		
		Args:
			channel (int): Channel number, zero-indexed.
			value (any): Value to assign to channel.
		
		Returns:
			None
		'''
		chan = self.get_valid_ch(channel)
		self.channel_data[chan] = value
	
	def get_ch_val(self, channel:int):
		''' Get the value assigned to the channel.
		
		Args:
			channel (int): Channel to get, zero-indexed.
		
		Returns:
			Value assigned to channel. Any type. Returns None if value
			has not been assigned to channel yet.
		'''
		chan = self.get_valid_ch(channel)
		if not self.ch_is_populated(chan):
			self.log.error(f"Cannot return channel value; channel has not been populated.")
			return None
		return self.channel_data[chan]
	
	def ch_is_populated(self, channel:int):
		''' Checks if the specified channel has been assigned a value.
		
		Args:
			channel (int): Channel to get, zero-indexed.
		
		Returns:
			bool: True if channel has been assigned a value.
		'''
		
		return (channel in self.channel_data.keys())

class DataEntry:
	''' Used in driver.data to describe a measurement result and its
	accompanying time.'''
	
	def __init__(self):
		self.update_time = None
		self.value = []

class Driver(ABC):
	
	#TODO: Modify all category and drivers to pass kwargs to super
	def __init__(self, address:str, log:plf.LogPile, expected_idn:str="", is_scpi:bool=True, remote_id:str=None, host_id:HostID=None, client_id:str="", dummy:bool=False):
		
		self.address = address
		self.log = log
		self.is_scpi = is_scpi
		self.hid = host_id
		
		self.id = Identifier()
		self.expected_idn = expected_idn
		self.verified_hardware = False
		
		self.online = False
		self.rm = pv.ResourceManager()
		self.inst = None
		
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
		
		# Dummy variables
		self.dummy = dummy
		self.dummy_state_machine = {}
		
		#TODO: Automatically reconnect
		# Connect instrument
		self.connect()
	
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
		self.log.lowdebug(f"(Driver: >:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def debug(self, message:str, detail:str=""):
		self.log.debug(f"(Driver: >:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def info(self, message:str, detail:str=""):
		self.log.info(f"(Driver: >:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def warning(self, message:str, detail:str=""):
		self.log.warning(f"(Driver: >:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def error(self, message:str, detail:str=""):
		self.log.error(f"(Driver: >:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
		
	def critical(self, message:str, detail:str=""):
		self.log.critical(f"(Driver: >:q{self.id.short_str()}<) {message}", detail=f"({self.id}) {detail}")
	
	def connect(self, check_id:bool=True):
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default connect() function, instrument does recognize SCPI commands.", detail=f"{self.id}")
			return
		
		# Attempt to connect
		try:
			self.inst = self.rm.open_resource(self.address)
			self.online = True
			self.debug(f"Connected to address >{self.address}<.", detail=f"{self.id}")
			
			if check_id:
				self.query_id()
			
		except Exception as e:
			self.error(f"Failed to connect to address: {self.address}. ({e})", detail=f"{self.id}")
			self.online = False
	
	def modify_state(self, query_func:callable, param:str, value, channel:int=None):
		"""
		Updates the internal state tracker.
		
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
			prev_val = self.state[param]
			
			# Record ing log
			self.log.add_log(self.state_change_log_level, f"(Driver: >:q{self.id.short_str()}<) State modified; >{param}<=>:a{truncate_str(value)}<.", detail=f"Previous value was {truncate_str(prev_val)}")
			
			if channel is None:
				self.state[param] = value
			else:
				try:
					self.state[param].set_ch_val(1, value)
				except Exception as e:
					self.log.error(f"Failed to modify internal state. {e}")
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
					self.data[param].set_ch_val(1, value)
				except Exception as e:
					self.error(f"Failed to modify internal state. {e}")
			val = value
		else:
			val = query_func()
		
		return val
	
	def show_state(self):
		
		def split_param(s):
			before_brackets = s[:s.index("[")]
			inside_brackets = s[s.index("[")+1:s.index("]")]
			return before_brackets, inside_brackets
		
		for k, v in self.state.items():
			
			# Get name and unit strings
			name, unit = split_param(k)
			print(f"{name}:")
			print(f"    value: {truncate_str(v, limit=40)}")
			print(f"    unit: {unit}")
	
	def preset(self):
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default preset() function, instrument does recognize SCPI commands.", detail=f"{self.id}")
			return
		
		self.debug(f"Preset.", detail=f"{self.id}")
		
		self.write("*RST")
	
	def query_id(self):
		''' Checks the IDN of the instrument, and makes sure it matches up.'''
		
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
		
	def close(self):
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default close() function, instrument does recognize SCPI commands.")
			return
		
		self.inst.close()
	
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
		
	def write(self, cmd:str):
		''' Sends a SCPI command via PyVISA'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default write() function, instrument does recognize SCPI commands.")
			return
		
		if not self.online:
			self.warning(f"Cannot write when offline. ()")
			return
		
		if self.dummy:
			self.lowdebug(f"Writing to dummy: >{cmd}<.")
			return
		
		try:
			self.inst.write(cmd)
			self.lowdebug(f"Wrote to instrument: >{cmd}<")
		except Exception as e:
			self.error(f"Failed to write to instrument {self.address}. ({e})")
			self.online = False
	
	def id_str(self):
		pass
	
	def read(self):
		''' Reads via PyVISA'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default read() function, instrument does recognize SCPI commands.")
			return
		
		if not self.online:
			self.warning(f"Cannot write when offline. ()")
		
		try:
			rv = self.inst.read()
			self.lowdebug(f"Read from instrument: >:a{rv}<")
			return rv
		except Exception as e:
			self.error(f"Failed to read from instrument {self.address}. ({e})")
			self.online = False
			return None
	
	def query(self, cmd:str):
		''' Querys a command via PyVISA'''
		
		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default query() function, instrument does recognize SCPI commands.")
			return
		
		if not self.online:
			self.warning(f"Cannot write when offline. ()")
		
		try:
			rv = self.inst.query(cmd)
			self.lowdebug(f"Queried instrument, >{cmd}<, receiving >:a{rv}<.")
		except Exception as e:
			self.error(f"Failed to query instrument {self.address}. ({e})")
			self.online = False
			return None
		
		return rv
	
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
	
def bool_to_str01(val:bool):
	''' Converts a boolean value to 0/1 as a string '''
	
	if val:
		return "1"
	else:
		return "0"

def str01_to_bool(val:str):
	''' Converts the string 0/1 to a boolean '''
	
	if '1' in val:
		return True
	else:
		return False

def bool_to_ONFOFF(val:bool):
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