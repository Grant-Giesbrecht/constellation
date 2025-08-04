''' Minimum example for setting up a command line client using Heimdallr.
'''

from heimdallr.all import *
from heimdallr.networking.network import *
from heimdallr.networking.net_server import *
from heimdallr.networking.net_client import *
import argparse

# Parse Commandline arguments
parser = argparse.ArgumentParser()
parser.add_argument('-l', '--local', help="Use localhost instead of intranet address.", action='store_true')
args = parser.parse_args()

# Create socket - this is not protected by a mutex and should only ever be used by the main thread
if args.local:
	ip_address = "localhost"
else:
	ip_address = "192.168.1.116"

log = plf.LogPile()
ca = ClientAgent(log)

ca.set_addr(ip_address, 5555)
ca.connect_socket()

# Create remote instruments from address
scope1 = RemoteInstrument(ca, remote_address="oscilloscope_1")
scope2 = RemoteInstrument(ca, remote_address="oscilloscope_2")


