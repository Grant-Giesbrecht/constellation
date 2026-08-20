import pylogfile.base as plf
import base64
import struct
from abc import abstractmethod
from pyvicp import Client
import pyvisa as pv
import asyncio
import threading
from labmesh import DirectorClientAgent
from labmesh.util import prompt_network_password

class CommandRelay:
	''' Class used to relay commands from a "driver" (which defines the content of the
	instructions in commands) to the physical instrument. Using a CommandRelay object
	allows the Driver to relay commands directly to a instrument via Pyvisa, or to
	use a remote connection, with the difference being entirely invisible to the 
	driver.
	'''
	
	def __init__(self):
		
		self.address = ""
		self.log = None
	
	def configure(self, address:str, log:plf.LogPile):
		''' Configures the Relay with the appropriate address and log. Note
		that this is done after __init__ so that the Relay can be automatically
		configured by the driver in the driver's __init__ function, without the user
		having to change the address for both the relay and the driver.
		'''
		
		self.address = address
		self.log = log
	
	@abstractmethod
	def connect(self):
		''' Instructs relay to attempt to open the connection with the instrument,
		though note that this function will not be able to positively confirm a 
		successful connection.
		
		Returns:
			bool: False if connection was known to fail, else true.
		'''
		pass
	
	@abstractmethod
	def close(self):
		pass
	
	@abstractmethod
	def write(self):
		pass
	
	@abstractmethod
	def read(self):
		pass
	
	@abstractmethod
	def query(self):
		pass

	def query_binary(self, cmd:str, datatype:str='B'):
		''' Optional capability: queries a binary block (IEEE 488.2 #<n><count><bytes> format)
		from the instrument, returning the decoded values directly rather than as text. Not every
		relay can support this (e.g. a text-only network relay would have to re-encode raw bytes
		as JSON/text, risking corruption) - subclasses that can support it should override this.

		Args:
			cmd (str): SCPI query command (e.g. ":WAV:DATA?").
			datatype (str): struct format character for each data point (PyVISA convention -
				'B' for unsigned byte, 'h' for signed 16-bit, etc.).

		Returns:
			tuple: Element 0 = success status, element 1 = list of decoded values.
		'''
		raise NotImplementedError(f"{type(self).__name__} does not support query_binary().")

	def write_binary(self, cmd:str, values:list, datatype:str='B'):
		''' Optional capability: writes a SCPI command followed by an IEEE 488.2 definite-length
		binary block (#<n><count><bytes>). The inverse of query_binary(), and the practical way to
		push bulk data *to* an instrument - e.g. loading an arbitrary waveform into an AWG, where
		sending the same points as comma-separated ASCII would be several times larger and much
		slower.

		Args:
			cmd (str): SCPI command the block is attached to (e.g. ":WVDT WVNM,wave1,WAVEDATA,").
			values (list): Data points to send.
			datatype (str): struct format character for each data point (PyVISA convention -
				'B' for unsigned byte, 'h' for signed 16-bit, etc.).

		Returns:
			bool: Success status of the write.
		'''
		raise NotImplementedError(f"{type(self).__name__} does not support write_binary().")

class VICPDirectSCPIRelay(CommandRelay):
	''' A relay that directly connects to instruments via VICP and relays
	SCPI commands from a driver. This is only for LeCroy oscilloscopes because
	they require VICP instead of PyVisa.
	'''
	
	def __init__(self):
		super().__init__()
		
		self.inst = None
	
	def connect(self) -> bool:
		
		try:
			self.inst = Client(self.address)
			self.online = True
			self.log.debug(f"VICPDirectSCPIRelay attempting to open instrument at address >{self.address}<.")
		except:
			self.log.debug(f"VICPDirectSCPIRelay failed to open instrument at address >{self.address}<.")
			return False 
		return True
	
	def close(self) -> None:
		''' Attempts to close the connection to the physical 
		instrument.'''
		
		self.inst.close()
	
	def write(self, cmd:str) -> bool:
		''' Sends a SCPI command via PyVISA.
		
		Args:
			cmd (str): Command to write to instrument.
		
		Returns:
			bool: Success status of write.
		'''
		
		try:
			self.inst.send(cmd.encode())
			self.log.lowdebug(f"VICPDirectSCPIRelay wrote to instrument: >@:LOCK{cmd}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"VICPDirectSCPIRelay failed to write to instrument {self.address}. ({e})")
			return False
		
		return True
	
	def read(self) -> tuple:
		''' Reads data as a string from the instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.receive().decode()
			self.log.lowdebug(f"VICPDirectSCPIRelay read from instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"VICPDirectSCPIRelay failed to read from instrument {self.address}. ({e})")
			return False, ""
		
		return True, rv
	
	def query(self, cmd:str) -> tuple:
		''' Queries data as a string from the instrument.
		
		Args:
			cmd (str): Command to query from instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			self.inst.send(cmd.encode())
			rv = self.inst.receive().decode()
			self.log.lowdebug(f"VICPDirectSCPIRelay queried from instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"VICPDirectSCPIRelay failed to query instrument {self.address}. ({e})")
			return False, ""
		
		return True, rv

	def write_binary(self, cmd:str, values:list, datatype:str='B') -> bool:
		''' Writes a binary block over VICP. pyvicp has no equivalent of PyVISA's
		write_binary_values(), so the IEEE 488.2 definite-length header is built here:
		'#' + <number of digits in the count> + <count> + <payload bytes>.

		Args:
			cmd (str): SCPI command the block is attached to.
			values (list): Data points to send.
			datatype (str): struct format character per data point.

		Returns:
			bool: Success status of the write.
		'''

		try:
			payload = struct.pack(f"<{len(values)}{datatype}", *values)
			count = str(len(payload))
			header = f"{cmd}#{len(count)}{count}".encode()
			self.inst.send(header + payload)
			self.log.lowdebug(f"VICPDirectSCPIRelay wrote binary block to instrument: >:a{len(values)} values<.")
		except Exception as e:
			self.log.error(f"VICPDirectSCPIRelay failed to write binary block to instrument {self.address}. ({e})")
			return False

		return True

class DirectSCPIRelay(CommandRelay):
	''' A relay that directly connects to instruments via PyVisa and relays
	SCPI commands from a driver.
	'''
	
	def __init__(self, timeout_ms:float=30000, read_termination:str='\n', write_termination:str='\n'):
		super().__init__()

		# self.rm = pv.ResourceManager('@py')
		self.rm = pv.ResourceManager()
		self.inst = None
		# 30s default: confirmed against real Rigol DS1000Z hardware that a single max-size
		# :WAV:DATA? chunk (RAW mode) can legitimately take 15-20+ seconds - a shorter timeout
		# aborts mid-transfer, and the instrument keeps pushing the rest of that response into
		# the buffer regardless, corrupting whatever command/reply comes next.
		self.timeout_ms = timeout_ms
		# Explicit termination characters (standard SCPI convention). Confirmed against real
		# hardware this is required for a raw TCPIP ...::SOCKET resource (PyVISA otherwise has no
		# way to know a text reply is complete, and every query times out) - VXI-11 (...::INSTR)
		# resources frame messages independently of this and were confirmed unaffected by setting
		# it explicitly too, so it's applied uniformly rather than only for socket resources.
		self.read_termination = read_termination
		self.write_termination = write_termination

	def connect(self) -> bool:

		try:
			self.inst = self.rm.open_resource(self.address)
			# PyVISA resources have no timeout guarantee unless set explicitly - without this, a
			# malformed/unexpected reply (e.g. a binary block query that doesn't get a real
			# binary block back) can block forever waiting for bytes that never arrive, hanging
			# the whole process instead of raising a catchable error.
			self.inst.timeout = self.timeout_ms
			self.inst.read_termination = self.read_termination
			self.inst.write_termination = self.write_termination
			self.online = True
			self.log.debug(f"DirectSCPIRelay attempting to open instrument at address >{self.address}<.")
		except:
			self.log.debug(f"DirectSCPIRelay failed to open instrument at address >{self.address}<.")
			return False
		return True
	
	def close(self) -> None:
		''' Attempts to close the connection to the physical 
		instrument.'''
		
		self.inst.close()	
	
	def write(self, cmd:str) -> bool:
		''' Sends a SCPI command via PyVISA.
		
		Args:
			cmd (str): Command to write to instrument.
		
		Returns:
			bool: Success status of write.
		'''
		
		try:
			self.inst.write(cmd)
			self.log.lowdebug(f"DirectSCPIRelay wrote to instrument: >@:LOCK{cmd}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to write to instrument {self.address}. ({e})")
			return False
		
		return True
	
	def read(self) -> tuple:
		''' Reads data as a string from the instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.read()
			self.log.lowdebug(f"DirectSCPIRelay read from instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to read from instrument {self.address}. ({e})")
			return False, ""
		
		return True, rv
	
	def query(self, cmd:str) -> tuple:
		''' Queries data as a string from the instrument.
		
		Args:
			cmd (str): Command to query from instrument.
		
		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''
		
		try:
			rv = self.inst.query(cmd)
			self.log.lowdebug(f"DirectSCPIRelay queried instrument: >@:LOCK{rv}@:UNLOCK<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to query instrument {self.address}. ({e})")
			return False, ""

		return True, rv

	def query_binary(self, cmd:str, datatype:str='B') -> tuple:
		''' Queries a binary block (IEEE 488.2 #<n><count><bytes> format) from the instrument via
		PyVISA's query_binary_values(), which parses the block header and terminator for us.

		Args:
			cmd (str): SCPI query command (e.g. ":WAV:DATA?").
			datatype (str): struct format character for each data point (PyVISA convention).

		Returns:
			tuple: Element 0 = success status, element 1 = list of decoded values.
		'''

		try:
			rv = self.inst.query_binary_values(cmd, datatype=datatype, container=list)
			self.log.lowdebug(f"DirectSCPIRelay queried binary block from instrument: >:a{len(rv)} values<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to query binary block from instrument {self.address}. ({e})")
			return False, []

		return True, rv

	def write_binary(self, cmd:str, values:list, datatype:str='B') -> bool:
		''' Writes a binary block via PyVISA's write_binary_values(), which builds the IEEE
		488.2 block header for us.

		Args:
			cmd (str): SCPI command the block is attached to.
			values (list): Data points to send.
			datatype (str): struct format character per data point (PyVISA convention).

		Returns:
			bool: Success status of the write.
		'''

		try:
			# is_big_endian=False matches the little-endian convention used across the network
			# relay, so a block reads back identically whether it was sent locally or remotely.
			self.inst.write_binary_values(cmd, values, datatype=datatype, is_big_endian=False)
			self.log.lowdebug(f"DirectSCPIRelay wrote binary block to instrument: >:a{len(values)} values<.")
		except Exception as e:
			self.log.error(f"DirectSCPIRelay failed to write binary block to instrument {self.address}. ({e})")
			return False

		return True

class RemoteTextCommandRelayClient(CommandRelay):
	''' A CommandRelay that tunnels write/read/query calls over labmesh to a remote
	instrument-adjacent process (a RemoteTextCommandRelayListener wrapped in a
	labmesh.RelayAgent, see below).

	This is the "smart client, dumb relay" half of Constellation's labmesh integration
	(see docs/labmesh_migration_plan.md): the Driver, its InstrumentState, dummy mode, and the
	category API all keep working exactly as they do locally - only this class's write/read/query
	go over the network instead of talking to pyvisa directly. Swapping DirectSCPIRelay() for
	this class (and pointing `address` at a relay_id instead of a VISA resource string) is the
	only thing that changes between local and networked use.
	'''

	def __init__(self, broker_address:str="127.0.0.1", broker_rpc:str="tcp://BROKER:5750", broker_xpub:str="tcp://BROKER:5752", timeout_s:float=10.0, binary_timeout_s:float=60.0):
		super().__init__()

		self.broker_address = broker_address
		self.broker_rpc = broker_rpc
		self.broker_xpub = broker_xpub
		self.timeout_s = timeout_s
		# Binary block reads get their own, much longer timeout. A full-memory :WAV:DATA? chunk
		# was confirmed against real hardware to take 15-20+ seconds, and DirectSCPIRelay allows
		# 30s for exactly that reason - so the 10s text timeout would abandon the RPC while the
		# instrument was still legitimately transferring, and the caller would see an empty
		# waveform rather than a slow one.
		self.binary_timeout_s = binary_timeout_s

		self.director = None
		self.relay_client = None

		self._loop = None
		self._loop_thread = None

	def _ensure_loop(self):
		''' Starts a background thread running a dedicated asyncio event loop the first time
		it's needed. All labmesh calls for this relay's lifetime run on this one loop, so the
		underlying ZMQ context/sockets persist across calls instead of being rebuilt on every
		single write/read/query (each of which is a plain synchronous call coming from Driver).
		'''

		if self._loop is not None:
			return

		ready = threading.Event()

		def _run_loop():
			loop = asyncio.new_event_loop()
			asyncio.set_event_loop(loop)
			self._loop = loop
			ready.set()
			loop.run_forever()

		self._loop_thread = threading.Thread(target=_run_loop, daemon=True)
		self._loop_thread.start()
		ready.wait()

	def _run(self, coro, timeout_s:float=None):
		''' Runs a coroutine on this relay's background event loop and blocks until it
		completes. This is the sync-to-async bridge between Driver's synchronous
		write/read/query and labmesh's async RelayClient.call().

		Args:
			coro: Coroutine to run.
			timeout_s (float): Optional override of self.timeout_s, for operations that are
				legitimately slower than a text query (see query_binary).
		'''

		self._ensure_loop()
		future = asyncio.run_coroutine_threadsafe(coro, self._loop)
		return future.result(self.timeout_s if timeout_s is None else timeout_s)

	def connect(self) -> bool:
		''' Connects to the broker and resolves this relay's `address` (set via configure(),
		same as every other CommandRelay) as a labmesh relay_id, to get a RelayClient for the
		remote instrument-adjacent process. '''

		# Read (or interactively prompt for, at most once per process) the shared mesh
		# password before connecting - see docs/labmesh_migration_plan.md.
		prompt_network_password()

		async def _connect():
			director = DirectorClientAgent(broker_address=self.broker_address, broker_rpc=self.broker_rpc, broker_xpub=self.broker_xpub)
			await director.connect()
			relay_client = await director.get_relay_agent(self.address)
			return director, relay_client

		try:
			self.director, self.relay_client = self._run(_connect())
			self.log.debug(f"RemoteTextCommandRelayClient connected to relay_id >{self.address}<.")
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to connect to relay_id >{self.address}<. ({e})")
			return False

		return True

	def close(self) -> None:
		''' Drops the connection. labmesh sockets aren't explicitly closed on the happy path
		(matches labmesh's own convention of running until killed); the background event-loop
		thread is a daemon thread so it won't block process exit. '''

		self.relay_client = None
		self.director = None

	def write(self, cmd:str) -> bool:
		''' Sends a SCPI command to the remote relay for it to write to the instrument.

		Args:
			cmd (str): Command to write to instrument.

		Returns:
			bool: Success status of write.
		'''

		if self.relay_client is None:
			self.log.error(f"RemoteTextCommandRelayClient cannot write - not connected.")
			return False

		try:
			ok = self._run(self.relay_client.call("write", {"cmd": cmd}))
			if ok:
				self.log.lowdebug(f"RemoteTextCommandRelayClient wrote to relay: >@:LOCK{cmd}@:UNLOCK<.")
			return bool(ok)
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to write via relay >{self.address}<. ({e})")
			return False

	def read(self) -> tuple:
		''' Reads data as a string from the instrument, via the remote relay.

		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''

		if self.relay_client is None:
			return False, ""

		try:
			ok, rv = self._run(self.relay_client.call("read", {}))
			if ok:
				self.log.lowdebug(f"RemoteTextCommandRelayClient read from relay: >:a{rv}<")
			return bool(ok), rv
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to read via relay >{self.address}<. ({e})")
			return False, ""

	def query(self, cmd:str) -> tuple:
		''' Queries data as a string from the instrument, via the remote relay.

		Args:
			cmd (str): Command to query from instrument.

		Returns:
			tuple: Element 0 = success status of read, element 1 = read string.
		'''

		if self.relay_client is None:
			return False, ""

		try:
			ok, rv = self._run(self.relay_client.call("query", {"cmd": cmd}))
			if ok:
				self.log.lowdebug(f"RemoteTextCommandRelayClient queried via relay: >:a{rv}<")
			return bool(ok), rv
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to query via relay >{self.address}<. ({e})")
			return False, ""

	def query_binary(self, cmd:str, datatype:str='B') -> tuple:
		''' Queries a binary block from the instrument via the remote relay.

		The block crosses the mesh as base64 rather than as a JSON list of numbers: a
		full-memory waveform is hundreds of thousands of points, which as JSON text is roughly
		3x the size of base64 and far slower to parse on both ends. The listener packs the
		decoded values little-endian with the same `datatype`, so this is byte-exact regardless
		of the two hosts' native endianness.

		Args:
			cmd (str): SCPI query command (e.g. ":WAV:DATA?").
			datatype (str): struct format character per data point (PyVISA convention).

		Returns:
			tuple: Element 0 = success status, element 1 = list of decoded values.
		'''

		if self.relay_client is None:
			self.log.error(f"RemoteTextCommandRelayClient cannot query_binary - not connected.")
			return False, []

		try:
			ok, payload = self._run(
				self.relay_client.call("query_binary", {"cmd": cmd, "datatype": datatype}),
				timeout_s=self.binary_timeout_s,
			)
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to query_binary via relay >{self.address}<. ({e})")
			return False, []

		if not ok:
			self.log.error(f"RemoteTextCommandRelayClient: remote relay >{self.address}< reported failure for query_binary.")
			return False, []

		try:
			raw = base64.b64decode(payload)
			item_size = struct.calcsize(f"<{datatype}")
			if item_size == 0 or len(raw) % item_size != 0:
				raise ValueError(f"{len(raw)} bytes is not a whole number of '{datatype}' items")
			values = list(struct.unpack(f"<{len(raw)//item_size}{datatype}", raw))
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to decode binary block from relay >{self.address}<. ({e})")
			return False, []

		self.log.lowdebug(f"RemoteTextCommandRelayClient read binary block via relay: >:a{len(values)} values<")
		return True, values

	def write_binary(self, cmd:str, values:list, datatype:str='B') -> bool:
		''' Sends a binary block to the instrument via the remote relay.

		Mirrors query_binary(): the payload is packed little-endian with `datatype` and base64'd,
		because labmesh's RPC envelope is strictly JSON (labmesh.util.dumps/loads are
		json.dumps/json.loads) and JSON has no bytes type. Sending the points as a JSON number
		list instead would be roughly 3x larger on the wire.

		Uses binary_timeout_s rather than the text timeout - pushing a full arbitrary waveform
		into an AWG is the same order of transfer as reading one back off a scope.

		Args:
			cmd (str): SCPI command the block is attached to.
			values (list): Data points to send.
			datatype (str): struct format character per data point.

		Returns:
			bool: Success status of the write.
		'''

		if self.relay_client is None:
			self.log.error(f"RemoteTextCommandRelayClient cannot write_binary - not connected.")
			return False

		try:
			payload = base64.b64encode(struct.pack(f"<{len(values)}{datatype}", *values)).decode("ascii")
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to pack {len(values)} values as '{datatype}'. ({e})")
			return False

		try:
			ok = self._run(
				self.relay_client.call("write_binary", {"cmd": cmd, "payload": payload, "datatype": datatype}),
				timeout_s=self.binary_timeout_s,
			)
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayClient failed to write_binary via relay >{self.address}<. ({e})")
			return False

		if ok:
			self.log.lowdebug(f"RemoteTextCommandRelayClient wrote binary block via relay: >:a{len(values)} values<")
		return bool(ok)

class RemoteTextCommandRelayListener:
	''' Wraps a local CommandRelay (DirectSCPIRelay or VICPDirectSCPIRelay) and exposes plain
	synchronous write/read/query/connect/close methods - this is the object handed to
	`labmesh.RelayAgent(relay_id, listener, ...)` on the machine physically wired to the
	instrument.

	It is deliberately driver-agnostic: it knows nothing about categories, InstrumentState,
	dummy mode, or which instrument model it's forwarding commands to - it only relays SCPI text,
	so one listener process works for any instrument Constellation supports, and the real Driver
	(with its state tracking) lives in whichever process actually owns the instrument instead of
	being pinned to the machine next to the bench. See docs/labmesh_migration_plan.md.
	'''

	def __init__(self, address:str, log:plf.LogPile, local_relay:CommandRelay=None):

		self.address = address
		self.log = log

		self.local_relay = local_relay if local_relay is not None else DirectSCPIRelay()
		self.local_relay.configure(address, log)

	def connect(self) -> bool:
		return self.local_relay.connect()

	def close(self) -> None:
		self.local_relay.close()

	def write(self, cmd:str) -> bool:
		return self.local_relay.write(cmd)

	def read(self) -> list:
		''' Returns [success:bool, value:str] - a list rather than a tuple since this return
		value crosses the network as JSON, which has no tuple type. '''
		return list(self.local_relay.read())

	def query(self, cmd:str) -> list:
		''' Returns [success:bool, value:str] - see read(). '''
		return list(self.local_relay.query(cmd))

	def query_binary(self, cmd:str, datatype:str='B') -> list:
		''' Reads a binary block from the instrument and returns it as
		[success:bool, base64_str] so it can cross the mesh as JSON.

		The local relay hands back already-decoded values; those are re-packed little-endian
		with the same `datatype` and base64'd. Explicit little-endian (rather than native byte
		order) means the bench machine and the controlling machine don't have to share an
		architecture.

		Returns [False, ""] if the local relay can't do binary reads at all - VICPDirectSCPIRelay
		doesn't implement query_binary, and that must surface as a clean failure here rather than
		as an unhandled NotImplementedError inside the RelayAgent.
		'''

		try:
			ok, values = self.local_relay.query_binary(cmd, datatype=datatype)
		except NotImplementedError as e:
			self.log.error(f"RemoteTextCommandRelayListener: local relay does not support query_binary. ({e})")
			return [False, ""]
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayListener failed to query binary block. ({e})")
			return [False, ""]

		if not ok:
			return [False, ""]

		try:
			packed = struct.pack(f"<{len(values)}{datatype}", *values)
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayListener failed to pack {len(values)} values as '{datatype}'. ({e})")
			return [False, ""]

		self.log.lowdebug(f"RemoteTextCommandRelayListener relaying binary block: >:a{len(values)} values, {len(packed)} bytes<")
		return [True, base64.b64encode(packed).decode("ascii")]

	def write_binary(self, cmd:str, payload:str, datatype:str='B') -> bool:
		''' Decodes a base64 binary block from the client and hands it to the local relay.

		Note the signature differs from CommandRelay.write_binary(): this takes the base64
		`payload` string that crossed the network, not a list of values, because that is what the
		RPC actually carries. The client packs, this unpacks.

		Returns False if the local relay can't do binary writes at all, rather than letting a
		NotImplementedError escape into the RelayAgent.
		'''

		try:
			raw = base64.b64decode(payload)
			item_size = struct.calcsize(f"<{datatype}")
			if item_size == 0 or len(raw) % item_size != 0:
				raise ValueError(f"{len(raw)} bytes is not a whole number of '{datatype}' items")
			values = list(struct.unpack(f"<{len(raw)//item_size}{datatype}", raw))
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayListener failed to decode binary block. ({e})")
			return False

		try:
			ok = self.local_relay.write_binary(cmd, values, datatype=datatype)
		except NotImplementedError as e:
			self.log.error(f"RemoteTextCommandRelayListener: local relay does not support write_binary. ({e})")
			return False
		except Exception as e:
			self.log.error(f"RemoteTextCommandRelayListener failed to write binary block. ({e})")
			return False

		self.log.lowdebug(f"RemoteTextCommandRelayListener relayed binary block to instrument: >:a{len(values)} values<")
		return bool(ok)