""" Oscilloscope client - owns and drives the instrument over labmesh.


Demonstrates that using an instrument over the network looks the same as using it locally
(compare with examples/osc_hardware_demo.py and examples/osc_dummy_demo.py): this script
instantiates the exact same RigolDS1000Z Driver class, just with a RemoteTextCommandRelayClient
instead of the default DirectSCPIRelay - only the `relay=` constructor argument changes, and
`address` is now looked up as a labmesh relay_id instead of a VISA resource string. See
docs/labmesh_migration_plan.md for the full rationale ("smart client, dumb relay").

Run order:
  1. python osc_broker.py --toml labmesh.toml
  2. python scpi_relay_node.py <instrument VISA address> --relay_id osc-1 --toml labmesh.toml
  3. python osc_client.py --relay_id osc-1 --toml labmesh.toml

This script also starts a DriverStateBroadcaster so other, non-owning clients can observe this
instrument's state without needing to own it themselves - see osc_client_observer.py.
"""

import argparse
import sys
from constellation.all import *
from constellation.relay import RemoteTextCommandRelayClient
from constellation.networking.labmesh_net import DriverStateBroadcaster
from labmesh.util import read_toml_config

parser = argparse.ArgumentParser()
parser.add_argument("--relay_id", help="relay_id of the scpi_relay_node.py instance wired to the instrument.", default="osc-1")
parser.add_argument("--state_rpc", help="RPC bind address for this client's own DriverStateBroadcaster (must be unique on this host).", default="tcp://*:5851")
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
args = parser.parse_args()

toml_data = read_toml_config(args.toml)

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.DEBUG

# This is the only thing that differs from local use: a network relay instead of DirectSCPIRelay,
# and a relay_id (looked up via the broker) instead of a VISA resource string.
remote_relay = RemoteTextCommandRelayClient(
	broker_address=toml_data['broker']['address'],
	broker_rpc=toml_data['client']['broker_rpc'],
	broker_xpub=toml_data['client']['broker_xpub'],
)

osc = RigolDS1000Z(args.relay_id, log=log, relay=remote_relay)

if not osc.online:
	log.critical(f"Failed to connect to oscilloscope via relay_id >{args.relay_id}<. Exiting.")
	sys.exit()

osc.refresh_state()
osc.refresh_data()
osc.print_state()

# Broadcast this Driver's state so other clients can observe it (see osc_client_observer.py).
# Uses a distinct relay_id/port from the scpi_relay_node.py instance above - this is a second,
# independent labmesh RelayAgent, wrapping the fully-populated Driver instead of raw SCPI text.
broadcaster = DriverStateBroadcaster(
	f"{args.relay_id}-state", osc,
	broker_rpc=toml_data['relay']['broker_rpc'],
	rpc_bind=args.state_rpc,
	state_pub=toml_data['relay']['broker_xsub'],
	local_address=toml_data['relay']['default_address'],
	broker_address=toml_data['broker']['address'],
)
broadcaster.start()

# From here on, drive the instrument exactly as you would locally.
osc.set_div_volt(1, 0.5)
osc.print_state()
