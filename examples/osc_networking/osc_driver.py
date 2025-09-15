
import asyncio, sys, time, random, os
from typing import Dict, Any
from labmesh import RelayAgent
from labmesh.relay import upload_dataset
from labmesh.util import read_toml_config
import argparse
from constellation.all import *

# Create a parser
parser = argparse.ArgumentParser()
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
parser.add_argument("--relay_id", help="Relay ID to use on the network.", default="Inst-0")
parser.add_argument("--rpc", help="RPC port", default="")
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
	
	osc_1 = RigolDS1000Z("TCPIP0::192.168.1.74::INSTR", log=log)
	if not osc_1.online:
		return
	
	relay_id = "osc-1"
	agent = RelayAgent(relay_id, osc_1, state_interval=10.0)
	await asyncio.gather(agent.run(), periodic_upload(relay_id, ingest))
	
	# # Select a port to listen to, each driver needs a unique one
	rpc_addr = args.rpc #sys.argv[2] if len(sys.argv) > 2 else "tcp://*:5850"
	if rpc_addr == "":
		rpc_addr = toml_data['relay']['default_rpc_bind']
	
	# Create the RelayAgent to connect to the network
	agent = RelayAgent(relay_id, MockPSU(relay_id), broker_rpc=toml_data['relay']['broker_rpc'], state_interval=1.0, rpc_bind=rpc_addr, state_pub=toml_data['relay']['broker_xsub'], local_address=toml_data['relay']['default_address'], broker_address=toml_data['broker']['address'])
	
	# Get databank ingest address
	#TODO: This address should not be hardcoded
	# ingest = os.environ.get("LMH_BANK_INGEST_CONNECT", "tcp://127.0.0.1:5761")
	ingest = toml_data['bank']['ingest_bind'].replace("*", toml_data['bank']['default_address'])
	
	# Launch all tasks (RelayAgent's task's and periodic upload)
	await asyncio.gather(agent.run(), periodic_upload(relay_id, ingest))

if __name__ == "__main__":
	
	# Run main function
	asyncio.run(main())