from pyfrost.base import *
from pyfrost.pf_client import *
from heimdallr.base import *

class NetworkCommand(Packable):
	''' Object used to represent a function call passed over the Heimdallr
	network to a remote instrument. '''
	
	def __init__(self, gc:GenCommand=None):
		super().__init__()
		
		# Target client to process command
		self.target_client = ""
		self.local_rcall_id = -1 # This is an ID local to the Client (ie. two different clients could send NetworkCommands with the same local_rcall_id) used to associate each remote_call with a corresponding network reply unambiguously.
		
		# Target instrument to execute command
		self.remote_id = ""
		self.remote_addr = ""
		
		# Command data
		self.function = {}
		self.args = {}
		self.kwargs = {}
		
		# Source of command
		self.source_client = ""
		self.timestamp = str(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')) # From time object is created on server, not time it is sent from client.
		
		# Initialize from gc if provided
		if gc is not None:
			
			self.remote_id = gc.data['REMOTE-ID']
			self.remote_addr = gc.data['REMOTE-ADDR']
			self.local_rcall_id = gc.data['LOCAL_RCALL_ID']
			
			self.function = gc.data['FUNCTION']
			self.args = gc.data['ARGS']
			self.kwargs = gc.data['KWARGS']
			
			try:
				pipe_idx = self.remote_addr.find("|")
				self.target_client = self.remote_addr[:pipe_idx]
			except Exception as e:
				print(f"Failed to get target-client from remote address.", detail=f"Error message: {e}") #TODO: This needs to be removed or a log
				self.target_client = ""
			
	def set_manifest(self):
		
		self.manifest.append("target_client")
		self.manifest.append("local_rcall_id")
		
		self.manifest.append("remote_id")
		self.manifest.append("remote_addr")
		
		self.manifest.append("function")
		self.manifest.append("args")
		self.manifest.append("kwargs")
		
		self.manifest.append("source_client")
		self.manifest.append("timestamp")

class NetworkReply(Packable):
	''' Object used to represent the return value (and success state) of a
	function call passed over the Heimdallr	network to a remote instrument
	via a NetworkCommand object. '''
	
	def __init__(self, gc:GenCommand=None):
		super().__init__()
		
		# Target client to process command
		self.replyto_client = ""
		self.local_rcall_id = -1 # This is an ID local to the Client (ie. two different clients could send NetworkCommands with the same local_rcall_id) used to associate each remote_call with a corresponding network reply unambiguously.
		
		# Instrument that executed command
		self.remote_id = ""
		self.remote_addr = ""
		
		# Return value
		self.rcall_status = False # Did remote call execute successfully?
		self.rval = None # Return value from instrument call (if successful)
		
		self.timestamp = str(datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')) # From time object is created on server, not time it is sent from client.
		
		# Initialize from gc if provided
		if gc is not None:
			
			self.remote_id = gc.data['REMOTE-ID']
			self.remote_addr = gc.data['REMOTE-ADDR']
			self.local_rcall_id = gc.data['LOCAL_RCALL_ID']
			
			self.rcall_status = gc.data['RCALL_STATUS']
			self.rval = gc.data['RVAL']
			self.replyto_client = gc.data['REPLYTO_CLIENT']
	
	def set_manifest(self):
		
		self.manifest.append("replyto_client")
		self.manifest.append("local_rcall_id")
		
		self.manifest.append("remote_id")
		self.manifest.append("remote_addr")
		
		self.manifest.append("rcall_status")
		self.manifest.append("rval")
		
		self.manifest.append("timestamp")

class DriverManager:
	''' Accepts a number of driver instances and allows them to be interacted with
	over a network.
	'''
	
	#TODO: Implement multi-threading
	
	def __init__(self, log:plf.LogPile, ca:ClientAgent=None):
		
		self.drivers = {} # Dictionary mapping key=remote-addr to value=Driver-objects
		self.log = log
		
		# ClientAgent - if None, will ignore all network operations
		self.ca = ca 
	
	def route_command(self, command:NetworkCommand) -> bool:
		''' Executes a NetworkCommand by translating the relevant command into
		a function call for the target driver. Returns a tuple with index 0: true false for succcess status, and index 1: return value from called function.'''
		
		# Verify that target is in lookup table
		if not command.remote_addr in self.drivers:
			self.log.error(f"DriverManager unable to route NetworkCommand because requested remote-addr is not in lookup table.")
			return (False, None)
		
		# Translate NetworkCommand into a function call
		
		# Try to grab function handle
		try:
			# Try to get function handle from driver object
			func_handle = getattr(self.drivers[command.remote_addr], command.function)
		except AttributeError as e:
			self.log.error(f"DriverManager unable to route command because the specified driver does not have the requested function.", detail=f"driver remote address={command.remote_addr}, function={command.function}(), error message: ({e})")
			return (False, None)
		
		# Unpack args into list from dict
		#TODO: Iterate over this better (should be ints, but should double check all consecutive and starting ffrom  zero and in order. )
		args = []
		for k, v in command.args.items():
			args.append(v)
		
		# Try to call function
		try:
			rval = func_handle(*args, **command.kwargs)
		except TypeError as e:
			self.log.error(f"DriverManager unable to route command because the specified function did not accept the provided arguemnts.", detail=f"Error message: ({e}). Args={args}, kwargs={command.kwargs}")
			return (False, None)
		
		# Return success code and return value from function (may be NOne)
		return (True, rval)
	
	def dl_reply(self, nc:NetworkCommand, status_rval:tuple) -> bool:
		''' Accepts a tuple from route_command() and sends the reply to the server
		via a GenCommand.'''
		
		try:
			if not status_rval[0]: # An error occured when routing the command or when the driver communicated with the intrument.
				
				# Send back a gencommand indicating: THis is a remote_call return, the value returned as an error, the original function call was X, the T/C client that should receive this message is Y.
				
				#TODO: Could include metadata like execution time
				gc = GenCommand("REMREPLY", {"RCALL_STATUS":False, "LOCAL_RCALL_ID":nc.local_rcall_id, "RVAL":None, "REMOTE-ID": nc.remote_id, "REMOTE-ADDR": nc.remote_addr, "REPLYTO_CLIENT": nc.source_client})
				
			else: # Sucecssfully routed command to driver
				
				# Send back a gencommand indicating: This is a remote_call return, the value returned successfully, the original function call was X, the T/C client that should receive this message is Y, and the return value from the function is Z (can be None).
				
				gc = GenCommand("REMREPLY", {"RCALL_STATUS":True, "LOCAL_RCALL_ID":nc.local_rcall_id, "RVAL":status_rval[1], "REMOTE-ID": nc.remote_id, "REMOTE-ADDR": nc.remote_addr, "REPLYTO_CLIENT": nc.source_client})
				
		except:
			self.log.error(f"DriverManager.route_command() returned an invalid tuple! This could is likely an error with route_command().")
			
			# Send back a gencommand indicating: THis is a remote_call return, the value returned as an error, the original function call was X, the T/C client that should receive this message is Y.
			gc = GenCommand("REMREPLY", {"RCALL_STATUS":False, "LOCAL_RCALL_ID":nc.local_rcall_id, "RVAL":None, "REMOTE-ID": nc.remote_id, "REMOTE-ADDR": nc.remote_addr, "REPLYTO_CLIENT": nc.source_client})
		
		# Send command to server and check for status
		if not self.ca.send_command(gc):
			self.log.error("Failed to send remote call reply. Received fail from server.")
			return False
		else:
			self.log.debug(f"Successfully sent remote call reply.")
			return True
		
	def add_instrument(self, instrument:Driver) -> bool:
		''' Adds an instrument to the DriverManager and will register it
		with the server if a ClientAgent was provided.
		
		Returns True if successfully added, else False.
		'''
		
		# Verify that instrument has a valid remote_addr
		if instrument.id.remote_addr == "":
			self.log.error(f"DriverManager cannot add an instrument without a populated remote-addr.")
			return False
		
		# Verify that instrument isn't already present
		if instrument.id.remote_addr in self.drivers:
			self.log.error(f"DriverManager cannot add an instrument whose remote-addr is already present in the driver list.")
			return False
		
		# Register instrument with server if possible
		if self.ca is not None:
			if not self.ca.register_instrument(instrument.id):
				self.log.error(f"DriverManager failed to register instrument with server.", detail=f"Instrument-ID: {instrument.id}")
			else:
				self.log.info(f"DriverManager successfully registered instrument with server.", detail=f"Instrument-ID: {instrument.id}")
		
		# Add to driver dictionary
		self.drivers[instrument.id.remote_addr] = instrument
		
		return True