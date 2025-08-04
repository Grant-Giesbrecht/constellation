from heimdallr.all import *
from heimdallr.networking.network import *
import argparse

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument('--ipaddr', help="Specify IP of server.")
parser.add_argument('--port', help="Specify port to connect to on server.", type=int)
parser.add_argument('-d', '--detail', help="Show detailed log messages.", action='store_true')
parser.add_argument('--loglevel', help="Set the logging display level.", choices=['LOWDEBUG', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], type=str.upper)
args = parser.parse_args()

# Select IP address
if args.ipaddr is None:
	ip_address = "localhost"
else:
	ip_address = args.ipaddr

# Select port
if args.port is None:
	port = 5555
else:
	port = int(args.port)

if __name__ == '__main__':
	
	# Create log object
	log = plf.LogPile()
	if args.loglevel is not None:
		log.set_terminal_level(args.loglevel)
	log.str_format.show_detail = args.detail
	
	# Create client agent
	ca = HeimdallrClientAgent(log)
	ca.set_addr(ip_address, port)
	ca.connect_socket()
	
	# login to server with default admin password
	ca.login("admin", "password")
	ca.register_client_id("driver_main")
	
	# Create a driver manager to handle the drivers
	dm = DriverManager(log, ca)
	
	# Create instrument driver and register with server
	scope1 = RigolDS1000Z("TCPIP0::192.168.1.20::INSTR", log, remote_id="Scope1", client_id=ca.client_id)
	
	# Add an instrument to DriverManager. This will register it with the server and 
	# add it to the DM's lookup table.
	dm.add_instrument(scope1)
	
	# Begin main loop s.t. this client executes the instructions from the server (which receives them from other clients)
	
	while True:
		
		# Listen for commands from server
		net_cmds = ca.dl_listen()
		
		# Check for error
		if net_cmds is None:
			log.error("An error occured while fetching NetworkCommands from the server.")
		
		# Process each command
		for nc in net_cmds:
			
			# CHeck for None (Shouldn't be possible)
			if nc is None:
				log.warning("A 'None' snuck into the NetworkCommands list!")
				continue
			
			# Route command to driver and look for return value
			status_rval = dm.route_command(nc)
			dm.dl_reply(nc, status_rval)