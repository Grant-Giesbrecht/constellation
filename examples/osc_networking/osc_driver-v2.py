
import asyncio, sys, time, random, os
from typing import Dict, Any
from labmesh import RelayAgent
from labmesh.relay import upload_dataset
import argparse

from constellation.all import *

parser = argparse.ArgumentParser()
parser.add_argument('address', help="VISA address of instrument to connect to.")
args = parser.parse_args()

async def periodic_upload(global_name: str):
	# pretend a big result every ~5s
	ingest = os.environ.get("LMH_BANK_INGEST_CONNECT", "tcp://127.0.0.1:5761")
	n = 0
	while True:
		await asyncio.sleep(60)
		payload = ("Result %d from %s\n" % (n, global_name)).encode() * 200000  # ~4MB
		did = await upload_dataset(ingest, payload, global_name=global_name, meta={"note":"demo"})
		print(f"[relay:{global_name}] uploaded dataset {did}")
		n += 1

async def main():
	log = plf.LogPile()
	log.str_format.show_detail = False
	log.terminal_level = plf.DEBUG
	
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
	
	relay = DirectSCPIRelay()
	agent = RelayAgent(gname, relay, state_interval=10.0)
	await asyncio.gather(agent.run(), periodic_upload(gname))

if __name__ == "__main__":
	asyncio.run(main())
