""" Generic instrument-adjacent labmesh relay node.

Wraps whichever local CommandRelay is appropriate for the instrument physically connected to this
machine (DirectSCPIRelay for most PyVISA-reachable instruments, VICPDirectSCPIRelay for LeCroy
scopes) in a RemoteTextCommandRelayListener, and exposes it over labmesh as a "dumb" SCPI-text
relay - it knows nothing about which instrument model is on the other end. The real Driver (with
its state tracking, category API, dummy mode, etc.) lives wherever the controlling script runs,
using constellation.relay.RemoteTextCommandRelayClient to talk to this node. See
docs/labmesh_migration_plan.md for the full rationale.

Run one of these per physical instrument connection, each with its own --relay_id and --rpc port.
"""

import asyncio
import argparse
from labmesh import RelayAgent
from labmesh.util import read_toml_config, prompt_network_password
from constellation.relay import RemoteTextCommandRelayListener, DirectSCPIRelay, VICPDirectSCPIRelay
import pylogfile.base as plf

parser = argparse.ArgumentParser()
parser.add_argument("address", help="VISA (or VICP) address of the instrument physically connected to this machine.")
parser.add_argument("--relay_id", help="relay_id to advertise this instrument as on the network.", required=True)
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
parser.add_argument("--rpc", help="RPC bind address for this relay. Defaults to the TOML file's [relay].default_rpc_bind.", default="")
parser.add_argument("--vicp", help="Use VICPDirectSCPIRelay instead of DirectSCPIRelay (needed for LeCroy scopes).", action="store_true")
args = parser.parse_args()

toml_data = read_toml_config(args.toml)

async def main():

	# Prompt for (or read from LMH_NETWORK_PASSWORD) the shared mesh password before joining -
	# see docs/labmesh_migration_plan.md. Non-interactive/headless deployments should pre-set
	# LMH_NETWORK_PASSWORD in the environment so this doesn't block on stdin.
	prompt_network_password(confirm=True)

	log = plf.LogPile()
	log.str_format.show_detail = False
	log.terminal_level = plf.DEBUG

	# Build the local (non-networked) relay for whichever transport this instrument needs
	local_relay = VICPDirectSCPIRelay() if args.vicp else DirectSCPIRelay()

	# Wrap it in the driver-agnostic listener and connect to the physical instrument
	listener = RemoteTextCommandRelayListener(args.address, log, local_relay=local_relay)
	if not listener.connect():
		log.critical(f"Failed to connect to instrument at address >{args.address}<. Exiting.")
		return

	rpc_addr = args.rpc or toml_data['relay']['default_rpc_bind']

	# Wrap the listener in a labmesh RelayAgent to expose it on the network
	agent = RelayAgent(
		args.relay_id, listener,
		broker_rpc=toml_data['relay']['broker_rpc'],
		rpc_bind=rpc_addr,
		state_pub=toml_data['relay']['broker_xsub'],
		local_address=toml_data['relay']['default_address'],
		broker_address=toml_data['broker']['address'],
	)

	await agent.run()

if __name__ == "__main__":
	asyncio.run(main())
