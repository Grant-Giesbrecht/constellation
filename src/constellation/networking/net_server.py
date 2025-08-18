from colorama import Fore, Style, Back

from pyfrost.base import *
from pyfrost.pf_server import *

from constellation.networking.network import *
from constellation.base import *

from Crypto.Random import get_random_bytes
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad, unpad
from dataclasses import dataclass

# TODO: Make this configurable and not present in most client copies
DATABASE_LOCATION = "userdata.db"
DL_LISTEN_TIMEOUT_OPTION = "DL_LISTEN_TIMEOUT"
DL_LISTEN_CHECK_OPTION = "DL_LISTEN_CHECK_TIME"
TC_LISTEN_TIMEOUT_OPTION = "TC_LISTEN_TIMEOUT"
TC_LISTEN_CHECK_OPTION = "TC_LISTEN_CHECK_TIME"

class ServerMaster:
	''' This class contains data shared between multiple clients. '''
	
	def __init__(self, master_log:plf.LogPile):
		
		#TODO: Add way of printing status of each of these (ie. lengths of the lists)
		
		# Create user configurable options
		self.options = ThreadSafeDict()
		
		# Add option: Timeout for DL-LISTEN GenCommands
		self.options.add_param(DL_LISTEN_TIMEOUT_OPTION)
		self.options.set(DL_LISTEN_TIMEOUT_OPTION, idx=0, val=0.5) # Set timeout (seconds) to 0.1
		
		# Add option: Period in between checks for DL-LISTEN GenCommands
		self.options.add_param(DL_LISTEN_CHECK_OPTION)
		self.options.set(DL_LISTEN_CHECK_OPTION, idx=0, val=0.2) # Set tvimeout (seconds) to 0.1
		
		# Add option: Timeout for TC-LISTEN GenCommands
		self.options.add_param(TC_LISTEN_TIMEOUT_OPTION)
		self.options.set(TC_LISTEN_TIMEOUT_OPTION, idx=0, val=0.1) # Set timeout (seconds) to 0.1
		
		# Add option: Period in between checks for TC-LISTEN GenCommands
		self.options.add_param(TC_LISTEN_CHECK_OPTION)
		self.options.set(TC_LISTEN_CHECK_OPTION, idx=0, val=0.05) # Set timeout (seconds) to 0.1
		
		# Initailize ThreadSafeDict object to track instruments
		self.master_instruments = ThreadSafeList() # (type = Identifier)
		self.master_net_cmd = ThreadSafeList() # Contains objects describing commands to route to driver/listener clients (Type = NetworkCommand)
		self.master_net_reply = ThreadSafeList() # Contains objects describing replies to network commands(Type = NetworkReply)
		self.master_client_ids = ThreadSafeList() # Contains a list of all client-ids currently present on the server (type = string)
		
		self.log = master_log
	
	def add_instrument(self, inst_id:Identifier) -> bool:
		''' Adds an instrument to the network. Returns boolean for success status.'''
		
		# Acquire mutex
		with self.master_instruments.mtx:
		
			# Check if remote_id or remote_addr are already used in master_instruments
			if len(self.master_instruments.find_attr("remote_id", inst_id.remote_id)) > 0:
				self.log.debug(f"Failed to add instrument because remote_id was already claimed on server.")
				return False
			if len(self.master_instruments.find_attr("remote_addr", inst_id.remote_addr)) > 0:
				self.log.debug(f"Failed to add instrument because remote_addr was already claimed on server.")
				return False
		
			# Add to list
			self.master_instruments.append(inst_id)
		
		return True

master_log = plf.LogPile()
serv_master = ServerMaster(master_log)

# Define parameters that go in sa.app_data (defined so harder to mistype)
CLIENT_ID = 'client_id'

def server_init_function(sa:ServerAgent):
	''' Initializes the server agent option with any preferences for the end application.
	Here it's just used to add feilds to the sa.app_data dict. Must return the modified
	sa. '''
	
	sa.app_data[CLIENT_ID] = ""
	return sa

def server_callback_send(sa:ServerAgent, gc:GenCommand):
	''' Function passed to ServerAgents to execute custom send-commands for Heimdallr
	 networks (ie. those without a return value). '''
	global serv_master
	
	if gc.command == "REG-INST": # Register instrument
		
		# Check fields present
		if not gc.validate_command(["REMOTE-ID", "REMOTE-ADDR", "CTG", "IDN-MODEL", "DVR"], log):
			return False
		
		nid = Identifier()
		nid.remote_addr = gc.data['REMOTE-ADDR']
		nid.remote_id = gc.data['REMOTE-ID']
		nid.ctg = gc.data['CTG']
		nid.dvr = gc.data['DVR']
		nid.idn_model = gc.data['IDN-MODEL']
		
		# Add instrument to database
		serv_master.add_instrument(nid)
		
		return True
	
	elif gc.command == "REG-CLIENT":
		
		# Check fields present
		if not gc.validate_command(["ID"], log):
			return False
		
		# Look for client already existing with this name
		with serv_master.master_client_ids.mtx:
			
			fidx = serv_master.master_client_ids.find(gc.data['ID'])
			if len(fidx) > 0: # client ID was found!
				return False
			
			# Add to list
			serv_master.master_client_ids.append(gc.data['ID'])
		
		# Save client-id to ServerAgent app_data
		sa.app_data[CLIENT_ID] = gc.data['ID']
		
		ncid = gc.data['ID']
		sa.log.debug(f"Registered client-id {ncid}")
		
		return True
	
	elif gc.command == "REMCALL":
		
		# Check fields present
		if not gc.validate_command(["LOCAL_RCALL_ID", "REMOTE-ID", "REMOTE-ADDR", "FUNCTION", "ARGS", "KWARGS"], log):
			return False
		
		# Create a NetworkCommand object
		nc = NetworkCommand(gc=gc)
		
		# Populate source-client ID
		nc.source_client = sa.app_data[CLIENT_ID]
		nc.timestamp = (datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'))
		
		# Add to master net command
		with serv_master.master_net_cmd.mtx:
			serv_master.master_net_cmd.append(nc)
		
		#TODO: Have the server periodically check that all NetworkCommand objects
		#      correspond to target_clients that exist. Purge those that are more 
		#      than X amount old.
		
		#TODO: This command should only be callable when the client is logged in.
		#      you should first check that a client has been authorized.
		
		return True
	
	elif gc.command == "REMREPLY":
		
		# Check fields present
		if not gc.validate_command(["RCALL_STATUS", "LOCAL_RCALL_ID", "REMOTE-ID", "REMOTE-ADDR", "RVAL", "REPLYTO_CLIENT"], log):
			return False
		
		# Create a NetworkCommand object
		nr = NetworkReply(gc=gc)
		
		# Add to master net command
		with serv_master.master_net_reply.mtx:
			serv_master.master_net_reply.append(nr)
		
		#TODO: Have the server periodically check that all NetworkReply objects
		#      correspond to target_clients that exist. Purge those that are more 
		#      than X amount old.
		
		#TODO: This command should only be callable when the client is logged in.
		#      you should first check that a client has been authorized.
		
		return True
	
	# Return None if command is not recognized
	return None

def server_callback_query(sa:ServerAgent, gc:GenCommand):
	''' Function passed to ServerAgents to execute custom query-commands for Heimdallr
	 networks (ie. those with a return value). '''
	global serv_master
	
	gd_err = GenData({"STATUS": False})
	
	if gc.command == "LOC-INST": # Locate instrument
		
		# Check fields present
		if not gc.validate_command(["REMOTE-ID", "REMOTE-ADDR"], log):
			gd_err.metadata['error_str'] = "Failed to validate command."
			return gd_err
		
		# Acquire mutex
		with serv_master.master_instruments.mtx:
		
			# Find remote-id or remote-addr, whichever are populated.
			if (gc.data['REMOTE-ID'] is not None) and (len(gc.data['REMOTE-ID']) > 0):
				fidx = serv_master.master_instruments.find_attr("remote_id", gc.data['REMOTE-ID'])
			else:
				fidx = serv_master.master_instruments.find_attr("remote_address", gc.data['REMOTE-ADDR'])
			
			# Make sure an entry was found
			if len(fidx) < 1:
				gd_err.metadata['error_str'] = "Failed to find specified instrument registered on server."
				return gd_err
			
			# Convert from list to first hit (int)
			fidx = fidx[0]
			
			# Access database
			rid = serv_master.master_instruments.read_attr(fidx, "remote_id")
			radr = serv_master.master_instruments.read_attr(fidx, "remote_addr")
			rdvr = serv_master.master_instruments.read_attr(fidx, "dvr")
			rctg = serv_master.master_instruments.read_attr(fidx, "ctg")
			ridn = serv_master.master_instruments.read_attr(fidx, "idn_model")
		
		# Populate GenData response
		gdata = GenData({"STATUS":True, "REMOTE-ID":rid, "REMOTE-ADDR": radr, "CTG":rctg, "DVR":rdvr, "IDN-MODEL":ridn})
		return gdata
	
	elif gc.command == "LIST-INST": # Return a list of all network-registered instruments
		
		#NOTE: Validation not performed because no additional parameters are expected
		
		# Initialize arrays
		rid = []
		radr = []
		rdvr = []
		rctg = []
		ridn = []
		
		# Hold master_instruments across multiple operations
		with serv_master.master_instruments.mtx:
		
			# Get length to iterate over
			param_len = serv_master.master_instruments.len()
			
			# Loop over length
			for fidx in range(param_len):
				
				# Access database
				rid.append(serv_master.master_instruments.read_attr(fidx, "remote_id"))
				radr.append(serv_master.master_instruments.read_attr(fidx, "remote_addr"))
				rdvr.append(serv_master.master_instruments.read_attr(fidx, "dvr"))
				rctg.append(serv_master.master_instruments.read_attr(fidx, "ctg"))
				ridn.append(serv_master.master_instruments.read_attr(fidx, "idn_model"))
		
		# Populate GenData response
		gdata = GenData({"STATUS":True, "REMOTE-ID":rid, "REMOTE-ADDR": radr, "CTG":rctg, "DVR":rdvr, "IDN-MODEL":ridn})
		return gdata
	
	elif gc.command == "DL-LISTEN": # Driver/Listener client is listening for new commands from server
		
		#NOTE: Validation not performed because no additional parameters are expected
		
		# Get timeout time from server master
		with serv_master.options.mtx:
			timeout_s = serv_master.options.read(DL_LISTEN_TIMEOUT_OPTION, 0)
			t_check_s = serv_master.options.read(DL_LISTEN_CHECK_OPTION, 0)
		
		# Check for option read error
		if timeout_s is None:
			timeout_s = 0.5
		if t_check_s is None:
			t_check_s = 0.2
		
		# Record start time
		t0 = time.time()
		
		# Loop until timeout or commands found
		fid = []
		while True:
			
			# Access mutex
			with serv_master.master_net_cmd.mtx:
				
				# Look for NetworkCommands addressed to this client-id
				fid = serv_master.master_net_cmd.find_attr("target_client", sa.app_data[CLIENT_ID])
				
				# If any commands are found...
				if len(fid) != 0:
					
					# Process each command
					nc_list = []
					for idx in fid:
						
						# Access each net command
						nc = serv_master.master_net_cmd.read(idx)
						
						# Pack NetworkCommand and add to list
						nc_list.append(nc.pack())
						
					# Create GenData for reply
					gdata = GenData({"STATUS":True, "NETCOMS":nc_list})
					serv_master.log.debug(f"Sending {len(nc_list)} NetComs to D/L client.", detail=f"List contents: {nc_list}")
					
					# Delete processed commands
					for idx in fid:
						serv_master.master_net_cmd.remove(idx)
						
					# Exit loop
					break
			
			# Break if timeout
			if time.time() >= t0 + timeout_s:
				
				# Create GenData for reply
				gdata = GenData({"STATUS":True, "NETCOMS":[]})
				
				break
			
			# Pause before checking again
			time.sleep(t_check_s)
		
		# Return packet
		return gdata
	
	elif gc.command == "TC-LISTEN": # Terminal/Command client is listening for replies from D/L clients via the server
		
		#NOTE: Validation not performed because no additional parameters are expected
		
		# Get timeout time from server master
		with serv_master.options.mtx:
			timeout_s = serv_master.options.read(TC_LISTEN_TIMEOUT_OPTION, 0)
			t_check_s = serv_master.options.read(TC_LISTEN_CHECK_OPTION, 0)
		
		# Check for option read error
		if timeout_s is None:
			timeout_s = 0.1
		if t_check_s is None:
			t_check_s = 0.05
		
		# Record start time
		t0 = time.time()
		
		# Loop until timeout or commands found
		fid = []
		while True:
			
			# Access mutex
			with serv_master.master_net_reply.mtx:
				
				# Look for NetworkReplies addressed to this client-id
				fid = serv_master.master_net_reply.find_attr("replyto_client", sa.app_data[CLIENT_ID])
				
				# If any replies are found...
				if len(fid) != 0:
					
					# Process each reply
					nr_list = []
					for idx in fid:
						
						# Access each net reply
						nr = serv_master.master_net_reply.read(idx)
						
						# Pack NetworkReply and add to list
						nr_list.append(nr.pack())
						
					# Create GenData for reply
					gdata = GenData({"STATUS":True, "NETREPLS":nr_list})
					serv_master.log.debug(f"Sending {len(nr_list)} NetReplys to T/C client.", detail=f"List contents: {nr_list}")
					
					# Delete processed commands
					for idx in fid:
						serv_master.master_net_reply.remove(idx)
						
					# Exit loop
					break
			
			# Break if timeout
			if time.time() >= t0 + timeout_s:
				
				# Create GenData for reply
				gdata = GenData({"STATUS":True, "NETREPLS":[]})
				
				break
			
			# Pause before checking again
			time.sleep(t_check_s)
		
		# Return packet
		return gdata
		
	# Return None if command is not recognized
	return None