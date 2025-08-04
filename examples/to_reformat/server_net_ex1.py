''' Minimum example for setting up a server using Heimdallr.
'''

from pyfrost.pf_server import *
from pylogfile.base import *
import argparse
from heimdallr.base import *
from heimdallr.networking.net_server import *

# Parse arguments
parser = argparse.ArgumentParser()
parser.add_argument('--ipaddr', help="Specify IP of server.")
parser.add_argument('--port', help="Specify port to connect to on server.", type=int)
parser.add_argument('-d', '--detail', help="Show detailed log messages.", action='store_true')
parser.add_argument('--loglevel', help="Set the logging display level.", choices=['LOWDEBUG', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'], type=str.upper)
args = parser.parse_args()

# Configure serv_master
serv_master.log.str_format.show_detail = args.detail
if args.loglevel is not None:
	serv_master.log.set_terminal_level(args.loglevel)

# Create socket - this is not protected by a mutex and should only ever be used by the main thread
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(SOCKET_TIMEOUT)

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

# Bind socket
sock.bind((ip_address, port))
sock.listen()

if __name__ == "__main__":
	
	# Run server main loop
	server_main(sock, query_func=server_callback_query, send_func=server_callback_send, sa_init_func=server_init_function)