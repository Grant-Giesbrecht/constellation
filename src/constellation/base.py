import pyvisa as pv
import copy
import pylogfile.base as plf
from pylogfile.base import mdprint
from constellation.relay import *
import numpy as np
import time
import inspect
import functools
from types import MethodType
from abc import ABC, abstractmethod
from socket import getaddrinfo, gethostname
import ipaddress
import fnmatch
import matplotlib.pyplot as plt
from stardust.serializer import Serializable, to_serial_dict, from_serial_dict, SERIALIZABLE_CLASS_REGISTRY
from stardust.io import hdf_to_dict, dict_to_hdf
import datetime
import numbers
from stardust.io import dict_summary
from colorama import Fore, Style
from enum import Enum

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
	its optional network nickname, rich-name, class type, and
	identification string provided by the instrument.'''

	def __init__(self):
		self.idn_model = "" # Identifier provided by instrument itself (*IDN?)
		self.ctg = "" # Category class of driver
		self.dvr = "" # Driver class

		self.remote_id = "" # Optional human-friendly nickname for this instrument on the network (distinct from its labmesh relay_id/address).

		self.address = "" # Instrument address to connect to (VISA resource string, or a labmesh relay_id if this Driver is behind a RemoteTextCommandRelayClient).

	def to_dict(self):
		''' Returns the instrument Identifier as a dictionary.

		Returns:
			Dictionary representing the identifier.
		'''

		return {"idn_model":self.idn_model, "ctg":self.ctg, "dvr":self.dvr, "remote_id":self.remote_id, "address":self.address}

	def short_str(self):
		dvr_short = self.dvr[self.dvr.rfind('.')+1:]
		if len(self.remote_id) > 0:
			return f"driver-class: {dvr_short}, remote-id: {self.remote_id}"
		else:
			return f"driver-class: {dvr_short}"

	def __str__(self):

		return f"idn_model: {self.idn_model}\ncategory: {self.ctg}\ndriver-class: {self.dvr}\nremote-id: {self.remote_id}\naddress: {self.address}"

	def __repr__(self):

		#TODO: Make this string a 1-line version. Because right now, if other objects were to also have multi-line reprs, then that would nest poorly.
		return f"idn_model: {self.idn_model}\ncategory: {self.ctg}\ndriver-class: {self.dvr}\nremote-id: {self.remote_id}\naddress: {self.address} at {hex(id(self))}"

class superreturn:
	''' Decorator for driver-level set_*/get_* methods. Runs the driver's own body (which talks
	to the instrument), then calls the same-named method on the category class above it, passing
	identical arguments and returning the category's return value. That is what lets state
	tracking in the category class run uniformly for every driver.

	A driver getter communicates its parsed value by simply RETURNING it - this decorator
	captures the return value into `self._super_hint`, which the category method reads. Drivers
	should not assign `self._super_hint` themselves.

	Implemented as a descriptor rather than a plain function decorator for one specific reason:
	the super() call needs the class the method was DEFINED on, and `__set_name__` is the only
	hook that provides it. The previous implementation used `super(type(self), self)`, where
	`type(self)` is the *runtime* class. That is the same class only while no driver is
	subclassed - the moment someone writes `class MyScope(RigolDS1000Z)`, `type(self)` stays
	`MyScope` on every hop, super() keeps re-finding `RigolDS1000Z`'s method, and each decorated
	call recurses until the stack blows.
	'''

	def __init__(self, func):
		self.func = func
		self.owner = None
		functools.update_wrapper(self, func)

	def __set_name__(self, owner, name):
		# Called by the interpreter right after the class body executes, with the defining class.
		self.owner = owner

	def __get__(self, obj, objtype=None):
		if obj is None:
			return self
		# Bind like a normal method: calling the result passes `obj` as the first argument.
		return MethodType(self.__call__, obj)

	def __call__(self, obj, *args, **kwargs):

		# Clear the hint every call. Without this, a driver getter that returns early (e.g.
		# RigolDS1000Z.get_coupling on an unrecognized reply) would leave the PREVIOUS call's
		# value in place for the category method to write into state.
		obj._super_hint = None

		# Call the driver's own body (but only if not in dummy mode), capturing whatever it
		# returns as the hint for the category method.
		if not obj.dummy:
			try:
				obj._super_hint = self.func(obj, *args, **kwargs)
			except Exception as e:
				obj.log.error(f"Failed to call driver function: >:a{self.func}< ({e}).")
				return None

		# Call super after, pass original arugments
		super_method = getattr(super(self.owner, obj), self.func.__name__)
		return super_method(*args, **kwargs)

def param_idx_to_str(params:list, indices:list=None) -> str:
	''' Creates a nicely formated plf-markdown string from a set of params
	and indices for modifying InstrumentStates.
	'''
	
	s = "["
	
	for idx, par in enumerate(params):
		
		# Add parameter names
		s = s + f">'{par}'<"
		
		# Add indices
		if indices is not None:
			if idx < len(indices):
				ind = indices[idx]
				s = s + f">:q[{ind}]<"
		
		# Add commas
		if idx != len(params)-1 :
			s = s + ", "
		else:
			s = s + "]"
	
	return s

class IndexedList(Serializable):
	''' Used in driver.state and driver.data structures to organize values
	for parameters which apply to more than one index.
	
	It also supports 'traces' for instruments that have both multiple traces and 
	multiple indices such as a vector network analyzer.
	
	NOTE: Iterating over an IndexedList will only iterate over populated values. Use populated_items() to
	iterate over (index, value) pairs instead, if the index of each thing iterated over is also needed.
	'''
	
	#TODO: Add some validation to the value type. I think they need to be JSON-serializable.
	
	__state_fields__ = ("first_index", "num_indices", "index_data", "validate_type_name")
	
	# Class-level defaults. stardust reconstructs via `cls.__new__(cls)` and never calls
	# __init__, so anything not in __state_fields__ simply does not exist on a restored object.
	# Without these, a restored IndexedList raised AttributeError on every write.
	_validate_type = None
	validate_type_name = ""
	
	def __init__(self, first_index:int, num_indices:int, validate_type=None, log:plf.LogPile=None):
		super().__init__()
		
		self.first_index = first_index
		self.num_indices = num_indices
		self.index_data = {}
		
		self.validate_type = validate_type
	
	@property
	def validate_type(self):
		''' The class stored values must be instances of, or None for no checking.
		
		Held as a class *name* in `validate_type_name` (which is what gets serialized) and
		resolved back to the class on demand via stardust's SERIALIZABLE_CLASS_REGISTRY - a
		class object itself can't be written to JSON/HDF. Resolution is lazy and cached, so a
		restored IndexedList regains its type checking the first time it's used.
		
		SCOPE - what this does and does not protect (measured, not assumed):
		
		This guards exactly one operation: *assigning a value into a slot of this list*. It is a
		container-level guard, not a schema validator. It answers "what kind of object lives in
		this list", and says nothing about the contents of those objects.
		
		Enforced:
		  - `lst[2] = value`
		  - `lst.set_idx_val(2, value)`   (a one-line delegate to __setitem__)
		  - `lst.append(value)` / `append(value, allow_expand=True)`
		  - `InstrumentState.set(params, value, indices=...)` *when the list slot itself is the
		    final target* - but note set() catches the TypeError, logs it, and returns False. It
		    is a soft failure with a log line, not a raised exception.
		
		NOT enforced:
		  - `InstrumentState.set(("channels", "div_volt"), v, indices=(2, None))` - i.e. walking
		    *through* the list to an attribute on the element. This terminates in a plain
		    setattr() and never touches __setitem__. This is the shape of nearly every real state
		    write in the codebase, so in practice validate_type sees very little traffic.
		  - direct pokes at `lst.index_data["idx-2"]`
		  - mutating an element in place (`lst[1].some_field = <anything>`)
		  - deserialization: stardust restores `index_data` wholesale without re-checking.
		
		Instrumenting a full construction + refresh_state()/apply_state() cycle across three
		drivers produced 13 calls to _check_type and 0 rejections; 12 of the 13 were a category
		__init__ pre-filling channel slots with objects it had just built itself. Treat this as
		documentation of intended element type (which is now serialized alongside the data) more
		than as a runtime safety net. See todo_list.md P16 for the proposal to extend add_param()
		into the checking that would actually cover the setattr path.
		'''
		
		if self._validate_type is not None:
			return self._validate_type
		
		name = getattr(self, "validate_type_name", "")
		if name:
			info = SERIALIZABLE_CLASS_REGISTRY.get(name)
			if info is not None:
				self._validate_type = info.cls
				return self._validate_type
		
		return None
	
	@validate_type.setter
	def validate_type(self, value):
		self._validate_type = value
		self.validate_type_name = value.__name__ if value is not None else ""
	
	def _check_type(self, value):
		''' Raises TypeError if `value` isn't an acceptable type for this list. '''
		
		expected = self.validate_type
		if expected is not None and not isinstance(value, expected):
			raise TypeError(f"Expected value of type '{expected}' but received value of type '{type(value)}'.")
	
	def clear(self):
		self.index_data = {}
	
	def get_populated(self):	
		''' Returns a list of all populated indices that can
		be iterated over. Easy way to iterate over all populated elements.
		
		for t in idx_list.get_populated():
			# do stuff to t
		'''
		
		populated_list = []
		
		for i in self.get_range():
			if self.idx_is_populated(i):
				populated_list.append(i)
		
		return populated_list
	
	def __getitem__(self, key:int):
		''' Returns the value at `key`, or None if that index has no value yet.
		Raises KeyError if `key` is outside [first_index, first_index+num_indices).
		'''
		
		self.get_valid_idx(key)
		return self.index_data.get(f"idx-{key}")
	
	def __setitem__(self, key:int, value):
		''' Stores `value` at `key`. Raises KeyError if the index is out of range, or
		TypeError if `validate_type` is set and `value` isn't an instance of it.
		'''
		
		self._check_type(value)
		self.get_valid_idx(key)
		self.index_data[f"idx-{key}"] = value
	
	def summarize(self, indent:str=""):
		
		out = ""
		
		found_none = True
		for ch, val in self.populated_items():
			found_none = False
			if ch != self.first_index:
				out = out + "\n"
			out += f"{indent}index {ch}:\n"
			
			# Values are usually InstrumentState objects, which know how to format themselves -
			# but nothing requires that (validate_type is optional), so fall back to a plain
			# repr rather than raising AttributeError on e.g. a float.
			if hasattr(val, "state_str"):
				out += val.state_str(indent=indent+"    ")
			else:
				out += plf.markdown(f"{indent}    >:a{protect_str(val, limit=40)}<") + "\n"
		
		if found_none:
			out += f"{indent}[>:qEmpty IndexedList<]"
		# for ch in range(self.first_index, self.first_index+self.num_indices):
		# 	if ch != self.first_index:
		# 		out = out + "\n"
		# 	val = self.get_idx_val(ch)
		# 	out = out + f"{indent}>:qindex {ch}<: >:a@:LOCK{truncate_str(val, 40)}@:UNLOCK<@:LOCK, ({type(val)})@:UNLOCK"
		
		out = plf.markdown(out) + "\n"
		return out
	
	def get_valid_idx(self, index:int) -> int:
		''' Validates that `index` is within this list's range, raising KeyError if not.
		
		Args:
			index (int): Index value to validate. Indices run from `first_index` (commonly 1,
				to match instrument channel numbering) up to first_index+num_indices-1 - they
				are NOT necessarily zero-based.
		
		Returns:
			int: The validated index number, unchanged.
		
		Raises:
			KeyError: If the index is outside the list's range.
		'''
		if index >= self.first_index+self.num_indices:
			raise KeyError(f"Max index exceeded")
		elif index < self.first_index:
			raise KeyError(f"Min index exceeded")
		else:
			return index
	
	def set_idx_val(self, index:int, value) -> None:
		''' Alias for `self[index] = value`. Kept because it reads more clearly at call sites
		that pass a computed index (e.g. InstrumentState.set). Deliberately a one-line delegate
		rather than a second implementation - the two used to be independent copies of the same
		logic, which is how behaviour drifts.
		
		Args:
			index (int): Index number. Indices run from first_index, which is often 1 to match
				instrument channel numbering - not necessarily 0.
			value (any): Value to assign to index.
		
		Returns:
			None
		'''
		self[index] = value
	
	def get_idx_val(self, index:int):
		''' Alias for `self[index]`. See set_idx_val() for why this delegates.
		
		Args:
			index (int): Index to get. Runs from first_index, not necessarily 0.
		
		Returns:
			Value assigned to index, or None if nothing has been assigned there yet.
		'''
		return self[index]
	
	def idx_is_populated(self, index:int):
		''' Checks if the specified index has been assigned a value.
		
		Args:
			index (int): Index to check. Runs from first_index, not necessarily 0.
		
		Returns:
			bool: True if index has been assigned a value.
		'''
		
		return (f"idx-{index}" in self.index_data.keys())
	
	def __iter__(self):
		''' Yields each populated value, in index order. This is a generator, so each
		call returns an independent iterator with its own position - safe to nest or
		use concurrently over the same IndexedList (unlike a hand-rolled __next__ that
		tracks position on self, which two overlapping iterations would corrupt).'''
		for idx in self.get_range():
			if self.idx_is_populated(idx):
				yield self.index_data[f"idx-{idx}"]

	def populated_items(self):
		''' Like __iter__, but yields (index, value) pairs so callers that need the
		index of each item (e.g. to find which index matches some condition) don't need
		separate iteration-position tracking. '''
		for idx in self.get_range():
			if self.idx_is_populated(idx):
				yield idx, self.index_data[f"idx-{idx}"]
	
	def append(self, value, allow_expand:bool=False) -> bool:
		''' Adds `value` to the lowest unpopulated index.
		
		Args:
			value: Value to store.
			allow_expand (bool): If True and every slot is already filled, grow num_indices by
				one and store the value in the new slot. If False (default), a full list is left
				untouched and False is returned.
		
		Returns:
			bool: True if the value was stored.
		'''
		
		# Scan over all indices, assign to first
		for ch in self.get_range():
			if not self.idx_is_populated(ch):
				self[ch] = value
				return True
		
		# Every slot is taken. Grow by one if the caller allows it.
		if allow_expand:
			# Type-check BEFORE mutating num_indices, so a rejected value can't leave the list
			# permanently one slot larger with nothing in it.
			self._check_type(value)
			new_idx = self.first_index + self.num_indices
			self.num_indices += 1
			self.index_data[f"idx-{new_idx}"] = value
			return True
		
		return False
	
	def get_range(self):
		return range(self.first_index, self.first_index+self.num_indices)
	
# Readability alias. Per-channel state is by far the most common use of IndexedList, and
# `ChannelList(1, 4, ...)` reads better than `IndexedList(1, 4, ...)` at those call sites. It is
# the same class, not a subclass - subclassing would create a second name in stardust's registry
# and break deserialization of anything already stored as an "IndexedList".
ChannelList = IndexedList

class InstrumentState(Serializable):
	""" Used to describe the state of a Driver or instrument.
	"""
	
	__state_fields__ = ("units", "is_data", "valid_params", "state_fragments")
	
	def __init_subclass__(cls, **kwargs):
		''' Makes every state subclass validate itself automatically, right after construction.
		
		`validate()` cross-checks the two lists a state class has to keep in sync - stardust's
		class-level `__state_fields__` serialization manifest, and the per-instance
		units/is_data registry built by `add_param()`. Drift between them means a parameter
		silently does not serialize, which is invisible until a saved state comes back missing
		a field.
		
		The guard only works if it is actually *called*, and relying on each state class to
		remember was not working - three of them never called it, and one had real drift as a
		result. Calling it from `Driver.__init__`/`discover_mixins()` instead is the wrong level:
		that only reaches `self.state` and the mixin fragments, and misses every nested state
		object (the per-channel/per-trace objects inside an IndexedList) and everything built
		lazily - `OscilloscopeMeasurementSetting` is constructed inside `add_measurement()`, long
		after `Driver.__init__` has returned.
		
		`__init_subclass__` fires once per subclass *definition*, which is the one point that
		cannot be forgotten. There is no instance yet, so it can't validate directly - it wraps
		the subclass's `__init__` so validation runs immediately after construction instead.
		
		Note this does NOT fire on deserialization: stardust rebuilds via `cls.__new__(cls)` and
		never calls `__init__`. That's correct - a restored object's field list comes from the
		file, and the class it was restored into was already validated when its own instances
		were built.
		'''
		
		# MUST cooperate: Serializable uses this hook too, for class registration and the
		# __state_fields__ parent-field merge. Call it first.
		super().__init_subclass__(**kwargs)
		
		orig_init = cls.__init__
		
		@functools.wraps(orig_init)
		def _validating_init(self, *args, **kw):
			
			orig_init(self, *args, **kw)
			
			# Only the most-derived class validates, so a B(A) hierarchy validates once rather
			# than once per level. NOTE: the obvious-looking optimization of tagging the wrapper
			# and refusing to wrap an already-wrapped __init__ is WRONG - a subclass that
			# inherits __init__ rather than defining one would then never validate at all.
			if type(self) is cls:
				self.validate()
		
		cls.__init__ = _validating_init
	
	def __init__(self, log:plf.LogPile=None):
		super().__init__()
		
		if log is not None:
			self.log = log
		else:
			self.log = plf.LogPile()
		
		self.surpress_warnings = False
		
		# Optional dictionary to contain unit information for the parameters.
		#  - Keys are names of variables
		#  - Values are the units for each
		# Note that these units are only for human readability/clarification. It
		# doesn't have to be any python type or object or something, it's just an
		# SI unit, or a clarifying phrase like 'bool' or 'num' or '1'.
		self.units = {}
		
		# Used to specify which parameters are "data" and don't need to be considered
		# state information.
		#
		# TODO: In the current version of Serializable there is no suppport for skipping certian
		# 'data' parameters, hwoever I'd like to add this in the future. HOwever, until then,
		# is_data is not used.
		self.is_data = []
		
		# List of all properly added parameters (helpful for listing state in printout)
		self.valid_params = []
		
		# Dict of state fragments for expanding with mixins
		self.state_fragments = {}
	
	def add_param(self, name:str, unit:str="", is_data:bool=False, value=None ):
		''' Adds a parameter in the __init__ function.
		
		Should only be used to add JSON serializable items, or IndexedLists,
		otherwise set_manifest won't be properly used.
		'''
		
		# Create parameter
		setattr(self, name, value)
		self.valid_params.append(name)
		
		# Populate unit and is_data
		self.units[name] = unit
		if is_data:
			self.is_data.append(name)
		
		# # Add to manifest
		# if isinstance(value, IndexedList):
		# 	self.obj_manifest.append(name)
		# else:
		# 	self.manifest.append(name)
	
	def validate(self) -> bool:
		''' Checks that everything in `__state_fields__` is in `add_param` and vice versa.
		
		Called automatically after construction of every InstrumentState subclass - see
		`__init_subclass__`. Reports through `self.log` only; a library must not write to stdout.
		
		Returns:
			bool: True if the two lists agree, False if drift was found (and warned about).
		'''
		
		missing_add_param = []
		missing_state_field = []
		
		# Check for values in __state_fields__ that were not placed in
		# add_param
		for sf in self.__state_fields__:
			if sf in InstrumentState.__state_fields__:
				self.log.lowdebug(f"{Fore.BLUE}{sf}{Style.RESET_ALL} in InstrumentState __state_fields__")
			elif sf in self.valid_params:
				self.log.lowdebug(f"{Fore.YELLOW}{sf}{Style.RESET_ALL} in self.valid_params")
			else:
				missing_add_param.append(sf)
				self.log.lowdebug(f"{Fore.RED}{sf}{Style.RESET_ALL} missing from add_param!")
		
		# Check for values in valid_params (from add_param) not in __state_fields__
		for vp in self.valid_params:
			
			if vp in self.__state_fields__:
				self.log.lowdebug(f"{Fore.YELLOW}{vp}{Style.RESET_ALL} in self.__state_fields__")
			else:
				self.log.lowdebug(f"{Fore.RED}{vp}{Style.RESET_ALL} missing from state_fields!")
				missing_state_field.append(vp)
				
		if len(missing_add_param) == 0 and len(missing_state_field) == 0:
			return True
		
		# Report through the log only. This used to also print() to stdout with colorama, which
		# is wrong for a library at the best of times and is far worse now that validation runs
		# on every state object ever constructed.
		if not self.surpress_warnings:
			
			if len(missing_add_param) > 0:
				self.log.warning(f"Validation failed in >:q{type(self).__name__}<: must call add_param() for >{missing_add_param}<.", detail="Listed in __state_fields__ but never registered via add_param(), so they carry no unit/is_data information.")
			
			# NOTE: this second branch used to be guarded by `len(missing_add_param) > 0` - a
			# copy-paste of the line above - so a class whose ONLY problem was a parameter
			# missing from __state_fields__ (the exact drift that stops a field serializing)
			# reported nothing at all.
			if len(missing_state_field) > 0:
				self.log.warning(f"Validation failed in >:q{type(self).__name__}<: must add to __state_fields__ for >{missing_state_field}<.", detail="Registered via add_param() but absent from the serialization manifest, so these fields silently do not serialize.")
		
		return False
	
	def get_unit(self, param:str):
		''' Attempts to return the unit for the specified param. Returns None
		if param invalid or if unit was not specified.'''
		
		if param in self.units:
			return self.units[param]
	
	def state_str(self, indent:str="") -> str:
		
		sout = ""
		
		# Add core label if fragments present
		if len(self.state_fragments) > 0:
			base_indent = indent
			sout += f"{base_indent}Core State:\n"
			indent += "    "
		
		# Print all valid params of core state
		for name in self.valid_params:
			
			# Get name and unit strings
			unit = self.get_unit(name)
			val = getattr(self, name)
			
			# Print value
			if isinstance(val, IndexedList):
				sout += plf.markdown(f"{indent}>{name}<:") + "\n"
				sout += val.summarize(indent="    "+indent)
			else:
				# sout += plf.markdown(f"{indent}>:q{name}<:")  + "\n"
				# sout += plf.markdown(f"{indent}    value: >:a{truncate_str(val, limit=40)}<")  + "\n"
				# sout += plf.markdown(f"{indent}    unit: >{unit}<")  + "\n"
				
				if val is not None:
					sout += plf.markdown(f"{indent}>{name}<: >:a{protect_str(val, limit=40)}<")
				else:
					sout += plf.markdown(f"{indent}>{name}<: >:qNone<")
				if unit is not None:
					sout += plf.markdown(f"     >:q[unit: <{protect_str(unit)}>:q]<") + "\n"
		
		# Add fragment labels
		if len(self.state_fragments) > 0:
			sout += f"{base_indent}State Fragments:\n"
			for frag, frag_obj in self.state_fragments.items():
				sout += f"{base_indent}    {frag}:\n"
				sout += frag_obj.state_str(f"{base_indent}        ")
			
		
		# Trim last newline
		if sout[-1:] == "\n":
			sout = sout[:-1]

		
		return sout
	
	def is_valid_type(self, test_obj):
		''' Checks if test_obj is a valid type for 
		'''
		if isinstance(test_obj, IndexedList):
			return True
		if isinstance(test_obj, Serializable):
			return True
		if isinstance(test_obj, dict):
			return True
		if isinstance(test_obj, numbers.Number): # TODO: How to save complex to HDF/JSON/dict?
			return True
		if isinstance(test_obj, str):
			return True
		#TODO: Should lists be accepted?
		
		return False
	
	def _get_fragment(self, fragment:str, action:str, params:tuple, indices:tuple):
		''' Looks up a state fragment by name, logging and returning None if it doesn't exist.
		Shared by set() and get() so their fragment handling can't drift apart. '''
		
		if fragment not in self.state_fragments:
			self.log.error(f"Cannot {action} state. Fragment >{fragment}< not found in >:q{type(self).__name__}<.", detail=f"params=({protect_str(params)}), indices=({protect_str(indices)})")
			return None
		
		return self.state_fragments[fragment]
	
	def _resolve(self, params:tuple, indices:tuple=None, action:str="access"):
		''' Walks `params` (descending into IndexedLists via the parallel `indices` tuple) and
		resolves the final slot it names.
		
		This is the single path-resolution routine behind both set() and get(). Any future change to
		path semantics now happens once, here.
		
		Args:
			params (tuple): Attribute names to walk, outermost first.
			indices (tuple): Parallel to `params` - indices[i] is used when params[i] resolves to
				an IndexedList. Entries for non-IndexedList params are ignored and may be None.
			action (str): Verb used in log messages ("set"/"get"), for readable errors.
		
		Returns:
			tuple: (container, key, is_indexed), or None if the path is invalid (already logged).
				is_indexed True  -> read/write with container.get_idx_val(key)/set_idx_val(key, v)
				is_indexed False -> read/write with getattr(container, key)/setattr(...)
		'''
		
		detail = f"params=({protect_str(params)}), indices=({protect_str(indices)})"
		
		# Guard the empty path. The old implementation left `list_at_top`, `idx` and `obj_under`
		# unbound here and died with an UnboundLocalError / setattr(None, ...).
		if params is None or len(params) == 0:
			self.log.error(f"Cannot {action} state. No parameters were given.", detail=detail)
			return None
		
		obj = self
		
		for idx, p in enumerate(params):
			
			is_last = (idx == len(params) - 1)
			
			# Check that parameter exists
			if not hasattr(obj, p):
				self.log.error(f"Cannot {action} state. Parameter >{p}< not found in >:q{type(obj).__name__}<.", detail=detail)
				return None
			
			parent, obj = obj, getattr(obj, p)
			
			# Plain attribute: if it's the last one, that's the slot we want.
			if not isinstance(obj, IndexedList):
				if is_last:
					return (parent, p, False)
				continue
			
			# An IndexedList needs a usable index from the parallel indices tuple.
			if indices is None or len(indices) <= idx or indices[idx] is None:
				self.log.error(f"Cannot {action} state. Parameter >{p}< is an IndexedList and requires >indices[{idx}]<.", detail=detail)
				return None
			
			# Validate the index is in range. Previously an out-of-range index raised a raw
			# KeyError out of set()/get(); now it's logged and reported like every other bad
			# input on this path, so callers only have one failure mode to handle.
			try:
				obj.get_valid_idx(indices[idx])
			except KeyError as e:
				self.log.error(f"Cannot {action} state. Index >{indices[idx]}< is out of range for >{p}<. ({e})", detail=detail)
				return None
			
			# The IndexedList slot itself is the target
			if is_last:
				return (obj, indices[idx], True)
			
			# Otherwise descend into the element it holds
			nxt = obj.get_idx_val(indices[idx])
			if nxt is None:
				self.log.error(f"Cannot {action} state. >{p}< index >{indices[idx]}< is not populated.", detail=detail)
				return None
			obj = nxt
		
		# Unreachable: the loop always returns on the final param.
		return None
	
	def set(self, params:tuple, value, indices:tuple=None, fragment:str=None) -> bool:
		''' Writes a value into the state at the path named by `params`/`indices`.
		
		Note that lists of objects MUST be stored in the IndexedList class.
		
		Args:
			params (tuple): Attribute names to walk, outermost first.
			value: Value to store.
			indices (tuple): Parallel to `params`; see _resolve().
			fragment (str): Optional state-fragment name to write into instead of this object.
		
		Returns:
			bool: True on success. False (with a logged error) on any invalid path.
		'''
		
		# If a state_fragment is being modified, hand off to it
		if fragment is not None:
			frag = self._get_fragment(fragment, "set", params, indices)
			if frag is None:
				return False
			return frag.set(params, value, indices=indices)
		
		target = self._resolve(params, indices=indices, action="set")
		if target is None:
			return False
		
		container, key, is_indexed = target
		
		try:
			if is_indexed:
				container.set_idx_val(key, value)
			else:
				setattr(container, key, value)
		except Exception as e:
			self.log.error(f"Cannot set state. Rejected value for {param_idx_to_str(params, indices=indices)}. ({e})", detail=f"value={protect_str(value)}")
			return False
		
		return True
	
	def get(self, params:tuple, indices:tuple=None, fragment:str=None):
		''' Reads the value at the path named by `params`/`indices`. Mirrors set().
		
		Args:
			params (tuple): Attribute names to walk, outermost first.
			indices (tuple): Parallel to `params`; see _resolve().
			fragment (str): Optional state-fragment name to read from instead of this object.
		
		Returns:
			The stored value, or None if the path is invalid (with a logged error).
		'''
		
		# If a state_fragment is being read, hand off to it
		if fragment is not None:
			frag = self._get_fragment(fragment, "get", params, indices)
			if frag is None:
				return None
			return frag.get(params, indices=indices)
		
		target = self._resolve(params, indices=indices, action="get")
		if target is None:
			return None
		
		container, key, is_indexed = target
		
		return container.get_idx_val(key) if is_indexed else getattr(container, key)
	

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

class FeatureUnavailable(RuntimeError):
	''' Exception for when features are requested that are not supported on
	the given driver.'''
	pass

def feature_unavailable(reason:str):
	''' Decorator marking a category method that THIS instrument genuinely cannot do.

	Not every instrument can implement every method of its category - real lab hardware has
	gaps. A Rigol DS1000E, for instance, cannot read or set its timebase over SCPI at all. The
	category class still declares those methods `@abstractmethod`, so a driver that simply omits
	them is not instantiable and the *entire* driver becomes unusable over one missing feature.

	Use this instead:

		@feature_unavailable("DS1000E cannot read the timebase over SCPI")
		def get_div_time(self):
			pass

	The decorated method satisfies the abstract method (the class becomes constructible), and
	calling it raises `FeatureUnavailable` naming both the method and the hardware reason. The
	body is never executed and is conventionally `pass`.

	Do NOT stack this with `@superreturn`: the point is that no driver body runs and nothing is
	written into `self.state`, since there is no value to track.

	Two further behaviours make this usable in practice:

	 - **Introspectable before the call.** `Driver.feature_is_available("get_div_time")` and
	   `Driver.unavailable_features()` report the marked methods and their reasons, so a GUI can
	   grey out a control instead of catching an exception after the user clicks it.
	 - **Skipped during state sweeps.** `refresh_state()`/`apply_state()`/`refresh_data()` call
	   every getter/setter unconditionally, so one `FeatureUnavailable` would abort the whole
	   sweep. Inside a sweep the marked method logs at debug and returns None instead of raising.
	   Direct calls still raise - silence is only appropriate when the caller is iterating over
	   everything rather than asking for this feature specifically.

	It raises in dummy mode too. Dummy mode simulates *this instrument*, and this instrument
	cannot do this - a dummy that quietly succeeded would hide the failure until hardware day.

	Args:
		reason (str): Human-readable hardware limitation, quoted in the exception and returned by
			`unavailable_features()`. Say what the hardware can't do, not just "unsupported".
	'''

	def decorator(func):

		@functools.wraps(func)
		def wrapper(self, *args, **kwargs):

			detail = f"{type(self).__name__}.{func.__name__}() is unavailable: {reason}"

			# During a full-state sweep, skip rather than abort the sweep.
			if getattr(self, "_state_sweep_depth", 0) > 0:
				self.debug(f"Skipping unavailable feature >:a{func.__name__}<. ({reason})")
				return None

			raise FeatureUnavailable(detail)

		# The marker the introspection helpers look for. Set on the wrapper, which is what ends
		# up as the class attribute.
		wrapper.__feature_unavailable__ = reason

		return wrapper

	return decorator

class CheckOnline(Enum):
	''' Contains possible values for the Driver.check_online_on_error parameter.
	How check_online_on_error is set controls how a driver handles updating online
	status after an error occurs. 
	
	AUTO: Queries the instrument for a simple SCPI command if possible (is_scpi
		is True), and sets online by if instrument responds.
	SKIP_CHECK: Skips online check when error occurs and leaves online untouched.
	DEFAULT_OFFLINE: Skips online check and sets `online` to False.
	
	AUTO is the default behavior, and the recommended behavior unless bandwidth
	is of great concern.
	'''
	AUTO = "auto"
	SKIP_CHECK = "skip"
	DEFAULT_OFFLINE = "offline"

class Driver(ABC):
	
	#TODO: Modify all category and drivers to pass kwargs to super
	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay, state:InstrumentState, expected_idn:str="", is_scpi:bool=True, remote_id:str=None, host_id:HostID=None, dummy:bool=False, first_channel_num:int=1, first_trace_num:int=1):
		
		self.address = address
		self.log = log
		self.is_scpi = is_scpi
		self.hid = host_id
		
		self.id = Identifier()
		self.expected_idn = expected_idn
		self.verified_hardware = False
		
		#TODO: Will be replaced by Relay
		self.online = False
		# Default to a local PyVISA relay, constructed fresh per Driver. This default lives here
		# rather than in each driver's signature so that (a) a driver can't accidentally share one
		# relay object between instances via a mutable default argument, and (b) every driver
		# stays swappable onto a RemoteTextCommandRelayClient just by passing relay=.
		self.relay = relay if relay is not None else DirectSCPIRelay()
		
		# Configure relay with address and log
		self.relay.configure(self.address, self.log)
		
		# State tracking parameters
		self.dummy = False
		self.blind_state_update = False
		self.check_online_on_error = CheckOnline.AUTO # Controls how Driver responds to errors during instrument communication
		self.state = state # Should be an InstrumentState instance, created by the child class
		self.data = {} # Each value is a DataEntry instance
		self.state_change_log_level = plf.DEBUG
		self.data_state_change_log_level = plf.DEBUG
		self._super_hint = None # Last measured value 
		
		# Setup ID
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
		
		self.discover_mixins()
		
		# Depth counter consulted by @feature_unavailable - see _wrap_state_sweeps().
		self._state_sweep_depth = 0
		self._wrap_state_sweeps()
		
		#TODO: Automatically reconnect
		# Connect instrument
		self.connect()
	
	def _wrap_state_sweeps(self):
		''' Wraps this instance's refresh_state/apply_state/refresh_data so that methods marked
		`@feature_unavailable` are skipped inside them instead of aborting the sweep.
		
		Done here, once, rather than by editing every category's refresh_state/apply_state,
		because those three methods are abstract on Driver and every category writes its own -
		including categories that don't exist yet. Wrapping at __init__ means a new category gets
		the behavior for free and can't forget it.
		
		The wrapper is set as an *instance* attribute, which shadows the class method for normal
		calls but leaves the class attribute untouched - so `super().refresh_state()` chains
		inside category/driver code still resolve normally and are not double-wrapped.
		'''
		
		# init_dummy_state() belongs in this list for the same reason: it seeds defaults by
		# calling every setter in turn, so an unavailable one would abort construction.
		for name in ("refresh_state", "apply_state", "refresh_data", "init_dummy_state"):
			
			bound = getattr(self, name, None)
			if bound is None:
				continue
			
			setattr(self, name, self._as_state_sweep(bound))
	
	def _as_state_sweep(self, bound):
		''' Returns `bound` wrapped so `_state_sweep_depth` is raised for its duration. '''
		
		@functools.wraps(bound)
		def sweep(*args, **kwargs):
			self._state_sweep_depth += 1
			try:
				return bound(*args, **kwargs)
			finally:
				self._state_sweep_depth -= 1
		
		return sweep
	
	def unavailable_features(self) -> dict:
		''' Returns {method_name: reason} for every method this driver has marked
		`@feature_unavailable` - i.e. every part of its category API the hardware cannot do.
		
		Intended for capability introspection *before* calling: a GUI can grey out a control, and
		a script can branch, rather than catching `FeatureUnavailable` after the fact.
		'''
		
		out = {}
		
		for name in dir(type(self)):
			
			# getattr on the class, not the instance: avoids binding and avoids triggering
			# properties.
			attr = inspect.getattr_static(type(self), name, None)
			
			reason = getattr(attr, "__feature_unavailable__", None)
			if reason is not None:
				out[name] = reason
		
		return out
	
	def feature_is_available(self, name:str) -> bool:
		''' True if `name` is a method this driver can actually perform.
		
		Returns False both for features explicitly marked `@feature_unavailable` and for names
		the driver doesn't have at all, since neither can be called.
		'''
		
		attr = inspect.getattr_static(type(self), name, None)
		if attr is None:
			return False
		
		return getattr(attr, "__feature_unavailable__", None) is None
	
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
	
	def discover_mixins(self):
		''' This function discovers all mixin classes and incorporates their
		state fragments into the state_fragments dictionary.
		'''
		
		# Scan over all classes that are inherited
		for cls in type(self).__mro__:
			
			# Get __state_key__, a parameter defining how to refer to this mixin in the state_fragments dict
			key = getattr(cls, "__state_key__", None)
			
			# Get __state_fragment__, a parameter defining what kind of InstrumentState to assoc. with that mixin
			frag_cls = getattr(cls, "__state_fragment__", None)
			
			# Check if key and frag_cls were found. If not, skip..., not a mixin.
			if key and frag_cls and key not in self.state.state_fragments:
				
				# Add an instance of the class to the state_fragments
				self.state.state_fragments[key] = frag_cls(log=self.log)
		
		# Validate mixins
		for k, mix_class in self.state.state_fragments.items():
			
			try:
				mix_class.validate()
			except Exception as e:
				self.log.warning(f"Validation failed in {type(mix_class)}. ({e})")
	
	def check_online(self):
		''' Runs when an error occurs with insturment communication. This functions
		behavior regarding how it updates the self.online paramter is controlled
		by the self.check_online_on_error parameter.'''
		
		# Validate that self.check_online_on_error is of CheckOnline type
		if isinstance(self.check_online_on_error, CheckOnline):
			self.check_online_on_error = self.check_online_on_error
		elif self.check_online_on_error in (c.value for c in CheckOnline):
			# Convert raw string into enum
			self.check_online_on_error = CheckOnline(self.check_online_on_error)
		else:
			self.warning(f"Invalid type set for >self.check_online_on_error<. Defaulting to >CheckOnline.AUTO<.")
		
		# Skip check - return early
		if self.check_online_on_error == CheckOnline.SKIP_CHECK:
			self.debug(f">Driver.check_online()<: Skipping online status check, leaving self.online unchanged.")
			return
		elif self.check_online_on_error == CheckOnline.DEFAULT_OFFLINE:
			self.debug(f">Driver.check_online()<: Skipping online status check, defaulting to >OFFLINE<.")
			self.online = False
		elif self.check_online_on_error == CheckOnline.AUTO:
			
			self.debug(f">Driver.check_online()<: Performing automatic online status check.")
			
			# Verify is a SCPI instrument
			if not self.is_scpi:
				self.warning(f"Cannot use CheckOnline.AUTO for non-SCPI instruments. Defaulting to OFFLINE.")
				self.online = False
			
			# Check if instrument is online
			_, rv = self.relay.query("*IDN?") # Note we don't call self.relay() to avoid an infinite loop
			if len(rv) > 0:
				self.online = True
			else:
				self.online = False
			
			self.debug(f">Driver.check_online()<: self.online --\\> {self.online}")
	
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
				self.lowdebug(f"Wrote to instrument: >@:LOCK{cmd}@:UNLOCK<.")
		except Exception as e:
			self.error(f"Failed to write to instrument {self.address}. ({e})")
			self.check_online()
	
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
			self.check_online()
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
				
				# If insturment was switched to offline, check if true
				if not self.online:
					self.check_online()
				
				return ""
		except Exception as e:
			self.error(f"Failed to read from instrument {self.address}. ({e})")
			self.check_online()
			return ""

	def query_binary(self, cmd:str, datatype:str='B') -> list:
		''' Queries a binary block via the relay (see CommandRelay.query_binary). Only relays
		that implement it (typically a local DirectSCPIRelay, not a text-based network relay)
		can actually return data here - others raise NotImplementedError, which is caught and
		logged like any other relay failure. Updates self.online with success/failure.

		Args:
			cmd (str): SCPI query command (e.g. ":WAV:DATA?").
			datatype (str): struct format character for each data point (PyVISA convention).

		Returns:
			list: Decoded values, or [] on failure/offline/dummy/unsupported relay.
		'''

		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default query_binary() function, instrument does recognize SCPI commands.")
			return []

		# Abort if offline
		if not self.online:
			self.warning(f"Cannot query_binary when offline.")
			return []

		# Spoof if dummy
		if self.dummy:
			self.lowdebug(f"Reading binary from dummy")
			return []

		# Attempt to read
		try:
			self.online, rv = self.relay.query_binary(cmd, datatype=datatype)
			if self.online:
				self.lowdebug(f"Read binary block from instrument: >:a{len(rv)} values<")
				return rv
			else:
				self.check_online()
				return []
		except Exception as e:
			self.error(f"Failed to query binary block from instrument {self.address}. ({e})")
			self.check_online()
			return []

	def write_binary(self, cmd:str, values:list, datatype:str='B') -> bool:
		''' Writes a SCPI command followed by an IEEE 488.2 binary block via the relay (see
		CommandRelay.write_binary). The inverse of query_binary(): used to push bulk data *to* an
		instrument, e.g. loading an arbitrary waveform into an AWG, where the same points as
		comma-separated ASCII would be several times larger and much slower.

		Relays that can't do binary writes raise NotImplementedError, which is caught and logged
		like any other relay failure. Updates self.online with success/failure.

		Args:
			cmd (str): SCPI command the block is attached to.
			values (list): Data points to send.
			datatype (str): struct format character for each data point (PyVISA convention).

		Returns:
			bool: True on success. False on failure/offline/unsupported relay. In dummy mode
				returns True without touching the relay - nothing was written, but nothing
				failed either, so callers that check the result still behave normally.
		'''

		# Abort if not an SCPI instrument
		if not self.is_scpi:
			self.error(f"Cannot use default write_binary() function, instrument does recognize SCPI commands.")
			return False

		# Abort if offline
		if not self.online:
			self.warning(f"Cannot write_binary when offline.")
			return False

		# Spoof if dummy
		if self.dummy:
			self.lowdebug(f"Writing binary block to dummy: >@:LOCK{cmd}@:UNLOCK< (>:a{len(values)} values<).")
			return True

		# Attempt write
		try:
			self.online = self.relay.write_binary(cmd, values, datatype=datatype)
			if self.online:
				self.lowdebug(f"Wrote binary block to instrument: >@:LOCK{cmd}@:UNLOCK< (>:a{len(values)} values<).")
			return self.online
		except Exception as e:
			self.error(f"Failed to write binary block to instrument {self.address}. ({e})")
			self.check_online()
			return False

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
	
	def modify_state(self, query_func:callable, params:tuple, value, indices:tuple=None, fragment:str=None):
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
			indices (tuple): Tuple of ints, PARALLEL to `params` - indices[i] is used when
				params[i] resolves to an IndexedList, and is ignored (pass None) otherwise. It
				only needs to be long enough to cover the deepest IndexedList in the path.
				(An earlier version of this docstring said N-1 ints for N params; that was
				wrong - the tuples are index-aligned, not offset.)
			
		Returns:
			value, or result of query_func if provided.
		"""
		
		if self.dummy and query_func is None:
			# Dummy getter. `query_func is None` marks a get_* call, and in dummy mode the
			# driver's SCPI body never ran, so `value` (normally self._super_hint) is
			# meaningless - writing it would clobber the tracked state with None. Instead read
			# the tracked value back, which is what a real instrument would have reported.
			#
			# This is what lets get_* methods work in dummy mode WITHOUT a hand-written
			# dummy_responder case. Only genuinely synthetic getters (get_waveform,
			# get_measured_output, ...) still need @enabledummy + a dummy_responder entry.
			val = self.state.get(params, indices=indices, fragment=fragment)
			self.log.add_log(self.state_change_log_level, f"(>:q{self.id.short_str()}<) State read (dummy): {param_idx_to_str(params, indices=indices)} -\\> >:a{truncate_str(val)}<.")
			return val
		
		if (query_func is None) or self.dummy or self.blind_state_update:
			# For these cases, the instrument is not queried (or at least, not again). Instead,
			# the `value` parameter is saved to the interal state tracker and returned.
			
			# prev_val = self.state.get(params, indices=indices)
			
			
			if self.state.set(params, value, indices=indices, fragment=fragment):
				self.log.add_log(self.state_change_log_level, f"(>:q{self.id.short_str()}<) State modified: {param_idx_to_str(params, indices=indices)} \\<- >:a{truncate_str(value)}<.") #, detail=f"Previous value was {truncate_str(prev_val)}")
			else:
				self.log.add_log(self.state_change_log_level, f"(>:q{self.id.short_str()}<) Failed to modify state: {param_idx_to_str(params, indices=indices)} \\<- >:a{truncate_str(value)}<.") #, detail=f"Previous value
			val = value
		else:
			val = query_func()
		
		return val
				
	def print_state(self, pretty:bool=True):
		
		# Use pretty state formatting
		if pretty:
			print(self.state.state_str())
		
		# Print full dictionary
		else:
			state_dict = self.state_to_dict()
			dict_summary(state_dict, verbose=1) #TODO: Make this a flag

	
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
		
		# CreaTe data dictionary if requested, package output dict
		state_dict = to_serial_dict(self.state)
		state_dict['metadata'] = meta_dict
		
		return state_dict
	
	def poll(self) -> dict:
		''' Combination of refresh_state and state_to_dict() to meet the expectations
		of the RelayAgent in labmesh.
		'''
		
		self.refresh_state()
		return self.state_to_dict()
	
	def dump_state(self, filename:str, include_data:bool=False):
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
	
	def load_state_dict(self, state_dict:dict) -> bool:
		''' Loads a state from a dictionary. Note that this only updates the 
		internal state, it does NOT apply the state to the hardware. To do this,
		the `apply_state()` function must be used.
		
		Args:
			state_dict (dict): State dictionary to apply to the internal state. 
		
		Returns:
			bool: True if state is succesfully loaded.
		'''
		
		self.state = from_serial_dict(state_dict)
		
		return True
	
	def restore_state(self, filename:str):
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
	
	def refresh_mixins(self):
		''' Passes calls of refresh_state to all mixins as well as the core class.

		refresh_state/apply_state are optional hooks a mixin's state fragment may implement -
		none currently do, so fragments without one are silently skipped rather than treated as
		an error.
		'''

		# Scan over state fragment objects (state_fragments is a dict keyed by mixin name)
		for frag in self.state.state_fragments.values():
			if hasattr(frag, "refresh_state"):
				frag.refresh_state()

	def apply_mixins(self):
		''' Passes calls of apply_state to all mixins as well as the core class.
		'''

		# Scan over state fragment objects (state_fragments is a dict keyed by mixin name)
		for frag in self.state.state_fragments.values():
			if hasattr(frag, "apply_state"):
				frag.apply_state()
	
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
	elif rd['unit'] not in ("dBm", "V", "Hz", "mA", "K", "uA", "dBV"):
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
	and the passed arguments.
	
	RESERVED FOR SYNTHETIC BEHAVIOR ONLY. Any set_*/get_* that maps to a plain state field
	needs no decorator at all - modify_state() handles dummy mode for those generically
	(setters store the value, getters read it back). Use this only where dummy mode has to
	*invent* something that isn't already in the state tracker, e.g. generating a waveform
	(get_waveform) or noisy meter readings (get_measured_output).
	
	Putting it on a plain setter is a bug: it bypasses modify_state() entirely, so the value
	is silently dropped unless dummy_responder() happens to have a matching case.'''
	
	def wrapper(self, *args, **kwargs):
		
		# If in dummy mode, activate the dummy_responder instead of attempting to interact with hardware
		if self.dummy:
			return self.dummy_responder(func.__name__, *args, **kwargs)
			
		# Call the source function (this should just be 'pass')
		return func(self, *args, **kwargs)

	return wrapper
