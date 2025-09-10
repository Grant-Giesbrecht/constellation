
import asyncio, sys, time, random, os
from typing import Dict, Any
from labmesh import RelayAgent
from labmesh.relay import upload_dataset
from labmesh.util import read_toml_config
import argparse
from constellation.all import *

parser = argparse.ArgumentParser()
# parser.add_argument('address', help="VISA address of instrument to connect to.")
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
parser.add_argument("--rpc", help="RPC port. Defaults to TOML file. Format as ZMQ port, e.g. tcp://*:5850", default="")
args = parser.parse_args()

# Read TOML file
toml_data = read_toml_config(args.toml)

async def periodic_upload(relay_id: str, ingest:str):
	# pretend a big result every ~5s
	
	# Main loop
	n = 0
	while True:
		
		# Pause...
		await asyncio.sleep(60)
		
		# Create a fake data payload
		payload = ("Result %d from %s\n" % (n, relay_id)).encode() * 200000  # ~4MB
		
		# Upload, get dataset_id
		did = await upload_dataset(ingest, payload, relay_id=relay_id, meta={"note":"demo"})
		
		# Print confirmation
		print(f"[relay:{relay_id}] uploaded dataset id={did} to {ingest}")
		n += 1

async def main():
	log = plf.LogPile()
	log.str_format.show_detail = False
	log.terminal_level = plf.DEBUG
	
	# # Select a port to listen to, each driver needs a unique one
	rpc_addr = args.rpc
	if rpc_addr == "":
		rpc_addr = toml_data['relay']['default_rpc_bind']
	
	# Create SCPI relay object
	spci_relay = DirectSCPIRelay()
	# NOTE: scpi_relay.configure(...) and .connect() are not being called because
	# the expectation is that the labmeshRelay will pass all commands from the
	# driver to the driver's labmeshRelay to this direct relay as RPCs. 
	
	# NOTE: We could make it work this way (below), however we don't want to do that because
	# then the backend (the relay thread) has to know it's state. That's not the constellation
	# way. Instead, constellation will just send and receive text commands.
	#
	# #NOTE: No need to explicitly create a Relay because an appropriate SCPI
	# # relay will automatically be created. Only the Client side needs to make a
	# # different type of relay.
	# osc_1 = RigolDS1000Z(args.address, log=log)
	# if not osc_1.online:
	# 	return
	#
	# It would be cool if the broker or client could spawn the relay threads (needs a better name
	# so its not confused with the relay class (in the clinet thread)). Lets instead call the 
	# relay threads "network-relay threads" or hardware-relay threads or relay-threads (however 
	# there are "relay" objects in both client and "relay/relay" threads. I could call them slave
	# threads but I think that is discouraged for obvious reasons. 
	
	relay_id = "osc-1"
	bank_ingest_addr = toml_data['bank']['ingest_bind'].replace("*", toml_data['bank']['default_address'])
	
	# Create the RelayAgent to connect to the network
	agent = RelayAgent(relay_id, spci_relay, broker_rpc=toml_data['relay']['broker_rpc'], state_interval=10, rpc_bind=rpc_addr, state_pub=toml_data['relay']['broker_xsub'], local_address=toml_data['relay']['default_address'], broker_address=toml_data['broker']['address'])
	
	await asyncio.gather(agent.run(), periodic_upload(relay_id, bank_ingest_addr))

if __name__ == "__main__":
	asyncio.run(main())
