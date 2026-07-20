""" Mesh broker - tracks what's connected to the network. Run this first. """

import asyncio
import argparse
from labmesh import DirectoryBroker
from labmesh.util import read_toml_config, prompt_network_password

parser = argparse.ArgumentParser()
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
args = parser.parse_args()

toml_broker = read_toml_config(args.toml)['broker']

if __name__ == "__main__":
	prompt_network_password(confirm=True)
	broker = DirectoryBroker(toml_broker['rpc_bind'], toml_broker['xsub_bind'], toml_broker['xpub_bind'])
	asyncio.run(broker.serve())
