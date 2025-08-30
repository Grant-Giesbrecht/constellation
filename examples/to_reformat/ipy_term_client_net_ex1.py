from constellation.all import *
from constellation.networking.network import *
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

# Create and initialize log
log = plf.LogPile()
if args.loglevel is not None:
	log.set_terminal_level(args.loglevel)
log.str_format.show_detail = args.detail

# Create client agent
ca = ConstellationClientAgent(log)
ca.set_addr(ip_address, port)
ca.connect_socket()

# login to server with default admin password
ca.login("admin", "password")
ca.register_client_id("terminal_main")

ca.get_network_instrument_list(print_ids=True)

# Create remote instruments from address
rem_osc1 = RemoteBasicOscilloscopeCtg(ca, log, remote_id="Scope1")

if rem_osc1.connected:
	log.info(f"Successfully connected rem_osc1 to remote instrument!")
	print(f"IDN provided by network server:")
	print(f"{Fore.YELLOW}{rem_osc1.id}{Style.RESET_ALL}")
else:
	log.error(f"Failed to connect rem_osc1 to remote instrument!")