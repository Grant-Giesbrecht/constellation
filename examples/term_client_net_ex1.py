from heimdallr.all import *
from heimdallr.networking.network import *
import argparse

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
	
	# Create and format log object
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
	ca.register_client_id("terminal_main")
	
	# Create client options
	copt = ClientOptions()
	
	ca.get_network_instrument_list(print_ids=True)
	
	# Create remote instruments from address
	# rem_osc1 = RemoteInstrument(ca, log, remote_address="192.168.1.117|TCPIP0::192.168.1.20::INSTR")
	# rem_osc1 = RemoteInstrument(ca, log, remote_id="Scope1")
	rem_osc1 = RemoteOscilloscopeCtg1(ca, log, remote_id="Scope1")
	
	# rem_osc1.locate_instrument(rem_osc1)
	
	if rem_osc1.connected:
		log.info(f"Successfully connected rem_osc1 to remote instrument!")
		print(f"IDN provided by network server:")
		print(f"{Fore.YELLOW}{rem_osc1.id}{Style.RESET_ALL}")
	else:
		log.error(f"Failed to connect rem_osc1 to remote instrument!")
	
	while True:
		
	# 	print(f"{Fore.GREEN}Remote Call:{Style.RESET_ALL}")
	# 	a = input(f"{Fore.CYAN}\tFunction: {Style.RESET_ALL}")
	# 	a = input(f"{Fore.YELLOW}\t(kw)arg (help for help): {Style.RESET_ALL}")
		
		a = input("Press enter to set volts/div to 1.")
		rem_osc1.remote_call("set_div_volt", 1, 1)
		
		