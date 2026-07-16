""" labmesh-based networking helpers for Constellation.

See docs/labmesh_migration_plan.md for the overall design: instrument-adjacent machines run a
driver-agnostic RemoteTextCommandRelayListener (constellation.relay) wrapped in a labmesh
RelayAgent, and whichever process owns a Driver talks to it via RemoteTextCommandRelayClient. This
module covers the other half - letting *other* (non-owning) clients observe a Driver's state.
"""

import asyncio
import threading
from labmesh import RelayAgent
from constellation.base import Driver

class DriverStateBroadcaster:
	''' Runs a labmesh.RelayAgent wrapping an already-connected Driver instance in a background
	thread, so other ("observer") clients can subscribe to its state via
	DirectorClientAgent.on_state(...), independent of however this Driver is itself being driven
	(a local CommandRelay, or a RemoteTextCommandRelayClient talking to a remote instrument).

	RelayAgent also exposes every public method on the wrapped object as an RPC, so this
	additionally lets observer clients call the Driver's own set_*/get_* methods directly.
	Whether that's desirable alongside a "smart client" that already owns the instrument, versus
	restricting observers to read-only state, is a policy decision, not a technical one -
	concurrent writers are allowed for now (see docs/labmesh_migration_plan.md's open questions).

	Because RelayAgent._serve_rpc calls the wrapped object's methods synchronously and
	Driver.write/read/query never `await`, an in-flight Driver.poll() and an incoming RPC call
	can't actually interleave mid-call - asyncio's single-threaded event loop only switches
	coroutines at `await` points, and neither poll() nor a Driver set_*/get_* method contains one.
	'''

	def __init__(self, relay_id:str, driver:Driver, broker_rpc:str, rpc_bind:str, state_pub:str, local_address:str="127.0.0.1", broker_address:str="127.0.0.1", state_interval:float=1.0):

		self.relay_id = relay_id
		self.driver = driver

		self.agent = RelayAgent(relay_id, driver, broker_rpc=broker_rpc, rpc_bind=rpc_bind, state_pub=state_pub, local_address=local_address, broker_address=broker_address, state_interval=state_interval)

		self._thread = None

	def start(self):
		''' Starts broadcasting in a background daemon thread with its own event loop, so the
		calling script's normal (synchronous) control flow is unaffected. '''

		if self._thread is not None:
			return

		def _run():
			loop = asyncio.new_event_loop()
			asyncio.set_event_loop(loop)
			loop.run_until_complete(self.agent.run())

		self._thread = threading.Thread(target=_run, daemon=True)
		self._thread.start()
