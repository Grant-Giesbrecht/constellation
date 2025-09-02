
import asyncio, sys, time, random, os
from typing import Dict, Any
from labmesh import DriverAgent
from labmesh.driver import upload_dataset
import argparse

from constellation.all import *

parser = argparse.ArgumentParser()
parser.add_argument('address', help="VISA address of instrument to connect to.")
args = parser.parse_args()

async def periodic_upload(service: str):
	# pretend a big result every ~5s
	ingest = os.environ.get("LMH_BANK_INGEST_CONNECT", "tcp://127.0.0.1:5761")
	n = 0
	while True:
		await asyncio.sleep(60)
		payload = ("Result %d from %s\n" % (n, service)).encode() * 200000  # ~4MB
		did = await upload_dataset(ingest, payload, service=service, meta={"note":"demo"})
		print(f"[driver:{service}] uploaded dataset {did}")
		n += 1

async def main():
	log = plf.LogPile()
	log.str_format.show_detail = False
	log.terminal_level = plf.DEBUG
	
	#NOTE: No need to explicitly create a Relay because an appropriate SCPI
	# relay will automatically be created. Only the Client side needs to make a
	# different type of relay.
	osc_1 = RigolDS1000Z(args.address, log=log)
	if not osc_1.online:
		return
	
	svc = "osc-1"
	agent = DriverAgent(svc, osc_1, state_interval=10.0)
	await asyncio.gather(agent.run(), periodic_upload(svc))

if __name__ == "__main__":
	asyncio.run(main())
