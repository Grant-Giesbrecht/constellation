""" Read-only oscilloscope observer client.

Demonstrates a "terminal node" (see docs/pages/what_why_heimdallr.md) that watches an instrument's
state without owning it: it only subscribes to the state PUB feed published by the owning
client's DriverStateBroadcaster (see osc_client.py) - it never opens a connection to the
instrument itself.

Nothing technical stops this script from also calling set_* methods via a RelayClient against the
same relay_id (concurrent writers are allowed for now - see docs/labmesh_migration_plan.md's open
questions); this example just demonstrates the read-only, observation-only use case.

Run order: start osc_broker.py, scpi_relay_node.py, and osc_client.py first (see osc_client.py's
docstring), then run this script pointed at the same --relay_id.
"""

import asyncio
import argparse
from labmesh import DirectorClientAgent
from labmesh.util import read_toml_config
from jarnsaxa import from_serial_dict
from ganymede import dict_summary

parser = argparse.ArgumentParser()
parser.add_argument("--relay_id", help="relay_id of the DriverStateBroadcaster to observe (osc_client.py's --relay_id, with '-state' appended).", default="osc-1-state")
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
args = parser.parse_args()

toml_data = read_toml_config(args.toml)

def print_state(relay_id, state):

	# Ignore state broadcasts from any other instrument on the mesh
	if relay_id != args.relay_id:
		return

	print(f"[state] {relay_id}:")
	inst_state = from_serial_dict(state)
	if isinstance(inst_state, dict):
		dict_summary(inst_state, verbose=1)
	else:
		print(inst_state.state_str())

async def main():

	client = DirectorClientAgent(
		broker_address=toml_data['broker']['address'],
		broker_rpc=toml_data['client']['broker_rpc'],
		broker_xpub=toml_data['client']['broker_xpub'],
	)
	await client.connect()

	client.on_state(print_state)

	while True:
		await asyncio.sleep(1)

if __name__ == "__main__":
	asyncio.run(main())
