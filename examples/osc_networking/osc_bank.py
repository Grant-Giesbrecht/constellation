""" Data bank node - stores waveforms uploaded by a driver script, serves them back out to
anyone that asks. """

import asyncio
import argparse
from labmesh import DataBank
from labmesh.util import read_toml_config, prompt_network_password

parser = argparse.ArgumentParser()
parser.add_argument("--bank_id", help="Bank ID to use on the network.", default="bank-0")
parser.add_argument("--toml", help="Set TOML configuration file", default="labmesh.toml")
args = parser.parse_args()

toml_data = read_toml_config(args.toml)
toml_bank = toml_data['bank']

if __name__ == "__main__":
	prompt_network_password(confirm=True)
	bank = DataBank(
		ingest_bind=toml_bank['ingest_bind'],
		retrieve_bind=toml_bank['retrieve_bind'],
		data_dir=toml_bank['default_data_dir'],
		broker_rpc=toml_bank['broker_rpc'],
		broker_xsub=toml_bank['broker_xsub'],
		bank_id=args.bank_id,
		heartbeat_sec=toml_bank['heartbeat_seconds'],
		broker_address=toml_data['broker']['address'],
		local_address=toml_bank['default_address'],
	)
	asyncio.run(bank.serve())
