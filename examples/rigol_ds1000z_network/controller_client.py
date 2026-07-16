""" Rigol DS1000Z measurement controller - the "owning" client.

This is the process that actually instructs the oscilloscope what to measure. It connects to the
scope over labmesh (via scpi_relay_node.py, which only relays raw SCPI text - see
docs/labmesh_migration_plan.md for why the Driver, not the relay, holds the state), configures the
timebase/channels/trigger, then periodically captures a waveform and uploads it to the databank
node. It also broadcasts its own Driver state so monitor_client.py can watch what it's doing
without needing to control the instrument itself.

Instantiating the Driver here is identical to local use (compare with
examples/osc_hardware_demo.py) - only the `relay=` argument and the fact that `address` is now a
labmesh relay_id (instead of a VISA resource string) differ.

Run order: see README.md in this directory.
"""

import asyncio
import argparse
import json
import time
from constellation.all import *
from constellation.relay import RemoteTextCommandRelayClient
from constellation.networking.labmesh_net import DriverStateBroadcaster
from labmesh.relay import upload_dataset
from labmesh.util import read_toml_config, prompt_network_password

parser = argparse.ArgumentParser()
parser.add_argument("--relay_id", help="relay_id of the scpi_relay_node.py instance wired to the scope.", default="rigol-1")
parser.add_argument("--state_rpc", help="RPC bind address for this client's own DriverStateBroadcaster (must be unique on this host).", default="tcp://*:5851")
parser.add_argument("--channels", help="Comma-separated channel numbers to enable and cycle through when capturing.", default="1,2")
parser.add_argument("--interval", help="Seconds between waveform captures/uploads.", type=float, default=10.0)
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
args = parser.parse_args()

toml_data = read_toml_config(args.toml)
channels = [int(c) for c in args.channels.split(",")]

# Read (or interactively prompt for) the shared mesh password before connecting to anything.
prompt_network_password()

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.DEBUG

# --- Connect to the instrument. This is the only thing that differs from local use: a network
# relay instead of DirectSCPIRelay, and a relay_id (looked up via the broker) instead of a VISA
# resource string. ---
remote_relay = RemoteTextCommandRelayClient(
	broker_address=toml_data['broker']['address'],
	broker_rpc=toml_data['client']['broker_rpc'],
	broker_xpub=toml_data['client']['broker_xpub'],
)

osc = RigolDS1000Z(args.relay_id, log=log, relay=remote_relay)

if not osc.online:
	log.critical(f"Failed to connect to oscilloscope via relay_id >{args.relay_id}<. Exiting.")
	raise SystemExit(1)

# --- Instruct the scope what to measure ---
osc.set_div_time(0.001)
osc.set_offset_time(0)
for ch in channels:
	osc.set_chan_enable(ch, True)
	osc.set_div_volt(ch, 0.5)
	osc.set_offset_volt(ch, 0)
	osc.set_coupling(ch, Oscilloscope.COUPLING_DC)

osc.set_trigger_source(channel=channels[0])
osc.set_trigger_level(0)
osc.set_trigger_mode(Oscilloscope.TRIG_AUTO)
osc.run_acquisition()

log.info(f"Oscilloscope configured (channels={channels}). Starting state broadcast and periodic waveform capture.")

# --- Broadcast this Driver's state so monitor_client.py can watch it, without owning it itself ---
broadcaster = DriverStateBroadcaster(
	f"{args.relay_id}-state", osc,
	broker_rpc=toml_data['relay']['broker_rpc'],
	rpc_bind=args.state_rpc,
	state_pub=toml_data['relay']['broker_xsub'],
	local_address=toml_data['relay']['default_address'],
	broker_address=toml_data['broker']['address'],
)
broadcaster.start()

# --- Periodically capture a waveform and upload it to the databank ---
async def periodic_capture():

	bank_ingest = toml_data['bank']['ingest_bind'].replace("*", toml_data['bank']['default_address'])
	ch_cycle = 0

	while True:

		# Cycle through the enabled channels each capture
		channel = channels[ch_cycle % len(channels)]
		ch_cycle += 1

		# get_waveform() is a plain, synchronous Driver call - same as local use - it just
		# happens to tunnel its SCPI commands over labmesh under the hood.
		waveform = osc.get_waveform(channel)

		payload = json.dumps({
			"relay_id": args.relay_id,
			"channel": channel,
			"captured_at": time.time(),
			"waveform": waveform,
		}).encode("utf-8")

		dataset_id = await upload_dataset(bank_ingest, payload, relay_id=args.relay_id, meta={"channel": channel})
		log.info(f"Uploaded waveform from channel {channel} as dataset >{dataset_id}< ({len(payload)} bytes).")

		await asyncio.sleep(args.interval)

if __name__ == "__main__":
	asyncio.run(periodic_capture())
