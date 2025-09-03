""" Oscilloscope hardware test
"""

import asyncio, os
from labmesh import DirectorClientAgent, RelayClient
from ganymede import dict_summary
from jarnsaxa import from_serial_dict
from constellation import *
from constellation.all import *
from PyQt6 import QtWidgets
# from constellation.ui import ConstellationWindow
import matplotlib.pyplot as plt
from constellation.instrument_control.oscilloscope.oscilloscope_gui import *
import sys

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.DEBUG

osc.refresh_state()
osc.refresh_data()

async def main():
	
	# Create oscilloscope object with remote relay
	remote_relay = RemoteTextCommandRelayClient()
	osc = RigolDS1000Z("TCPIP0::192.168.1.74::INSTR", log=log, relay=remote_relay)

	if not osc.online:
		log.critical(f"Failed to connect to oscilloscope. Exiting")
		sys.exit()
	
	client = DirectorClientAgent()
	await client.connect()

	print("Services:", await client.list_relay_ids())
	print("Banks:", await client.list_banks())
	
	def print_scope_state(gname:str, state:dict):
		print(f"Received instrument state for gname={gname}:")
		inst_state = from_serial_dict(state)
		if isinstance(inst_state , dict):
			dict_summary(inst_state, verbose=1)
		else:
			print(inst_state.state_str(pretty=True))
	
	# client.on_state(lambda gname, st: print(f"[state] {gname}: {st}"))
	client.on_state(print_scope_state)
	client.on_dataset(lambda info: print(f"[dataset] new {info}"))

	osc_dc = await client.get_relay_agent("osc-1")
	await osc_dc.call("set_div_volt", params=[4, 5])

	# auto-pick first bank and download datasets as they appear
	async def downloader(info):
		bank = await client.get_databank_agent(info.get("bank_id"))
		dest = f"./download_{info['dataset_id']}.bin"
		meta = await bank.download(info["dataset_id"], dest)
		print("[downloaded]", meta)

	client.on_dataset(lambda info: asyncio.create_task(downloader(info)))

	while True:
		await asyncio.sleep(1)

if __name__ == "__main__":
	asyncio.run(main())
