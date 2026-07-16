""" Read-only monitor client - the "just watching" client.

Demonstrates a "terminal node" (see docs/pages/what_why_heimdallr.md) that watches the
oscilloscope activity driven by controller_client.py without controlling the instrument itself:
it never connects to the scope or the relay, and never instantiates a Driver. It only subscribes
to two things over the mesh:
  - the state broadcast published by controller_client.py's DriverStateBroadcaster, and
  - dataset-upload notifications published whenever a waveform lands in the databank,
    which it then downloads from the databank to confirm the data really arrived.

Nothing technical stops this script from also calling set_* methods on the scope via its own
RelayClient (concurrent writers are allowed for now - see docs/labmesh_migration_plan.md's open
questions); this example just demonstrates the observe-only use case.

Run order: see README.md in this directory.
"""

import asyncio
import argparse
import json
import pathlib
from labmesh import DirectorClientAgent
from labmesh.util import read_toml_config, prompt_network_password
from jarnsaxa import from_serial_dict
from ganymede import dict_summary

parser = argparse.ArgumentParser()
parser.add_argument("--relay_id", help="relay_id of the DriverStateBroadcaster to observe (controller_client.py's --relay_id, with '-state' appended).", default="rigol-1-state")
parser.add_argument("--download_dir", help="Where to save downloaded waveform datasets.", default="./monitor_downloads")
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
args = parser.parse_args()

toml_data = read_toml_config(args.toml)
pathlib.Path(args.download_dir).mkdir(parents=True, exist_ok=True)

# Read (or interactively prompt for) the shared mesh password before connecting.
prompt_network_password()

def print_state(relay_id, state):

	# Ignore state broadcasts from any other instrument on the mesh
	if relay_id != args.relay_id:
		return

	print(f"\n[state] {relay_id}:")
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

	print("Relays on network:", await client.list_relay_ids())
	print("Banks on network:", await client.list_banks())

	client.on_state(print_state)

	async def on_new_dataset(info):

		print(f"\n[dataset] new upload: relay_id={info.get('relay_id')} dataset_id={info.get('dataset_id')} size={info.get('size')} bytes")

		# Confirm the data actually made it into the bank by downloading and inspecting it
		bank = await client.get_databank_agent(info.get("bank_id"))
		dest = f"{args.download_dir}/{info['dataset_id']}.json"
		meta = await bank.download(info["dataset_id"], dest)
		print(f"[dataset] downloaded {meta['size']} bytes to {dest} (sha256={meta['sha256']})")

		with open(dest, "r") as f:
			payload = json.load(f)
		wf = payload.get("waveform", {})
		n_points = len(wf.get("time_s", []))
		print(f"[dataset] channel={payload.get('channel')} captured_at={payload.get('captured_at')} points={n_points}")

	client.on_dataset(lambda info: asyncio.create_task(on_new_dataset(info)))

	print("Monitoring... (Ctrl+C to exit)")
	while True:
		await asyncio.sleep(1)

if __name__ == "__main__":
	asyncio.run(main())
