
import asyncio, os
from labmesh import LabClient, DriverClient
from ganymede import dict_summary
from jarnsaxa import from_serial_dict
from constellation import *

class PSUClient(DriverClient):
	async def set_voltage(self, *, value: float):
		return await super().call("set_voltage", {"value": value})
	async def set_output(self, *, on: bool):
		return await super().call("set_output", {"on": on})
	async def get_state(self):
		return await super().call("get_state", {})

async def main():
	client = LabClient()
	await client.connect()

	print("Services:", await client.list_services())
	print("Banks:", await client.list_banks())
	
	def print_scope_state(svc:str, state:dict):
		print(f"Received instrument state for svc={svc}:")
		inst_state = from_serial_dict(state)
		if isinstance(inst_state , dict):
			dict_summary(inst_state, verbose=1)
		else:
			print(inst_state.state_str(pretty=True))
	
	# client.on_state(lambda svc, st: print(f"[state] {svc}: {st}"))
	client.on_state(print_scope_state)
	client.on_dataset(lambda info: print(f"[dataset] new {info}"))

	osc_dc = await client.driver("osc-1")
	await osc_dc.call("set_div_volt", params=[4, 5])

	# auto-pick first bank and download datasets as they appear
	async def downloader(info):
		bank = await client.bank(info.get("bank_id"))
		dest = f"./download_{info['dataset_id']}.bin"
		meta = await bank.download(info["dataset_id"], dest)
		print("[downloaded]", meta)

	client.on_dataset(lambda info: asyncio.create_task(downloader(info)))

	while True:
		await asyncio.sleep(1)

if __name__ == "__main__":
	asyncio.run(main())
