# Migrating Constellation's networking from Pyfrost to labmesh

## Goal

Replace `pyfrost` (generic encrypted TCP client/server + accounts/lobbies) with `labmesh` (ZeroMQ mesh
built specifically for Constellation) as the networking layer, such that:

- Local (no-network) use of a `Driver` is completely unaffected — `RigolDS1000Z(address, log=log)` keeps
  working exactly as it does today.
- Networked use is opt-in and looks as close to local use as possible from the calling code's perspective.

This file inventories what exists today, names the one architectural fork this migration hinges on
(already partially decided in code comments — see below), and lists the concrete tasks to finish it.

## Current state

### To be removed: the pyfrost-based networking stack

- `src/constellation/networking/network.py` — `NetworkCommand`/`NetworkReply` (pyfrost `Packable` envelopes
  wrapping a remote method call: `function`/`args`/`kwargs`, `remote_id`/`remote_addr`, `source_client`),
  `DriverManager` (routes a `NetworkCommand` to a local `Driver` instance via `getattr(driver, function)`).
- `src/constellation/networking/net_client.py` — `ConstellationClientAgent(pyfrost.ClientAgent)`,
  `RemoteInstrument` (proxies a remote driver by building a `NetworkCommand`, sending it, then polling the
  server via `tc_listen()`/`get_sync_reply()` in a `time.sleep` loop until a matching `NetworkReply` shows
  up), and the `@remotefunction` decorator used to stub out proxy methods.
- `src/constellation/networking/net_server.py` — `ServerMaster` + `server_callback_send`/
  `server_callback_query`, registered into a pyfrost `ServerAgent` to implement `REG-INST`/`REG-CLIENT`/
  `REMCALL`/`REMREPLY`/`LOC-INST`/`LIST-INST`/`DL-LISTEN`/`TC-LISTEN` — a hand-rolled polling-based RPC
  protocol built on top of pyfrost's generic command/reply mechanism.
- `src/constellation/relay.py:222-`: `RemoteTextCommandRelayClient` and `RemoteTextCommandRelayListener` —
  unfinished/broken placeholders anticipating a remote `CommandRelay`. `RemoteTextCommandRelayClient`
  currently just duplicates `DirectSCPIRelay`'s local-pyvisa logic verbatim (not remote at all yet) and has
  a dead second `__init__(self): pass` at the bottom of the class that silently shadows the real one.
  `RemoteTextCommandRelayListener` is an empty stub.
- `pyproject.toml` depends on `pyfrost-network`; no `labmesh`/`pyzmq` dependency exists yet even though
  `labmesh` is already imported by the examples below.

None of this pyfrost machinery is used by any finished, working example — `examples/to_reformat/
client_net_ex1.py`, `remote_example1.py`, `term_client_net_ex1.py` (which exercise `RemoteInstrument`) are
already parked in `to_reformat/`, i.e. already known to be stale.

### Already in progress: labmesh prototypes

`examples/osc_networking/` has broker/bank scripts (`osc_broker.py`, `osc_bank.py` — trivial, just call
`DirectoryBroker().serve()` / `DataBank().serve()`) plus **two competing, unfinished drafts** of the
relay/client side:

- **v1** (`osc_driver.py` + `osc_client.py`): the relay-side process instantiates a full `RigolDS1000Z`
  `Driver` and hands the *whole Driver object* to `labmesh.RelayAgent`. The client never instantiates a
  `Driver` at all — it gets a bare `RelayClient` from `DirectorClientAgent.get_relay_agent(...)` and calls
  `await osc_dc.call("set_div_volt", params=[4, 5])` directly.
- **v2** (`osc_driver-v2.py` + `osc_client-v2.py`, the latter is broken/unfinished — references `osc` at
  module scope before it's defined, unconditionally imports PyQt6): the relay-side process wraps a bare
  `DirectSCPIRelay()` in `RelayAgent` (no `Driver`, no state) — a "dumb" text-command relay. The client
  instantiates the real `RigolDS1000Z` Driver *locally*, using a not-yet-implemented remote `CommandRelay`
  to tunnel raw `write`/`read`/`query` calls to the dumb relay.

`osc_driver-v2.py` contains this comment, which is the clearest existing statement of intent in the repo:

> We could make it work this way (below) \[= wrap the whole Driver on the relay side, i.e. v1\], however we
> don't want to do that because then the backend (the relay thread) has to know its state. That's not the
> constellation way. Instead, constellation will just send and receive text commands.

`Driver.poll()`'s docstring (`src/constellation/base.py:1208`) — "meet the expectations of the RelayAgent in
labmesh" — and `docs/pages/what_why_heimdallr.md`'s narrative of a "driver node" (holds the real connection)
versus a "terminal node" (remotely monitors/starts scripts) are both consistent with v2, not v1.

## The architectural decision this migration hinges on

**Decided: v2 ("dumb relay, smart client").** Concretely:

- The machine physically wired to an instrument runs a small, driver-agnostic process wrapping
  `DirectSCPIRelay`/`VICPDirectSCPIRelay` in `labmesh.RelayAgent`, exposing only `write`/`read`/`query`/
  `connect`/`close` over RPC. It knows nothing about categories, state, or dummy mode.
- Whichever process should "own" the instrument (the researcher's script/GUI, not necessarily the machine
  next to the bench) instantiates the real category+driver class exactly as it would locally, passing a new
  `CommandRelay` subclass that tunnels through labmesh instead of `DirectSCPIRelay`. **This is the entire
  point of Constellation's existing `CommandRelay` abstraction** (`src/constellation/relay.py`) — the
  `Driver`, its `InstrumentState`, dummy mode, and the category API don't need to change at all; only the
  `relay=` constructor argument changes between local and networked use. This satisfies the "same
  Constellation usage with or without a network" goal directly, whereas v1 would require calling code to use
  a completely different async RPC-stub API (`await osc_dc.call(...)`) when networked.
- For other, non-owning clients that just want to *observe* an instrument (Heimdallr's "terminal node"),
  the owning process can *additionally* wrap its own now-fully-populated `Driver` instance in a second
  `labmesh.RelayAgent` purely so `Driver.poll()` gets broadcast over the state PUB feed (and, if convenient,
  so its high-level methods are remotely callable too) — this reuses the same primitives, doesn't require
  new labmesh features, and matches `Driver.poll()`'s docstring.

Account/permission complexity from pyfrost (`UserDatabase`, per-user login) is explicitly **not** being
ported — see the shared-password section below for what replaces it. Concurrent writes from multiple
owning clients to the same instrument are explicitly **allowed for now** (no locking/ownership
enforcement) — see "Open questions" below, this is deliberately deferred, not overlooked.

## Task list

### 1. Finish the client-side remote `CommandRelay` — done
- [x] Rewrote `RemoteTextCommandRelayClient` (`src/constellation/relay.py`) to hold a
      `labmesh.DirectorClientAgent`/`RelayClient` instead of a `pyvisa.ResourceManager`. Removed the dead
      duplicate `__init__`.
- [x] Bridged sync-to-async with a persistent background thread running its own asyncio event loop
      (`_ensure_loop`/`_run`); `write`/`read`/`query` call `asyncio.run_coroutine_threadsafe(...).result(...)`.
- [x] `connect()` calls `prompt_network_password()` then resolves `self.address` (set via `configure()`,
      same as every other `CommandRelay`) as the labmesh `relay_id` via `DirectorClientAgent.get_relay_agent(...)`.
      Broker connection settings (`broker_address`/`broker_rpc`/`broker_xpub`) are constructor kwargs.
- [x] `write` returns `bool`; `read`/`query` return `(bool, str)` tuples, matching `DirectSCPIRelay`'s
      existing convention — the listener side (task 2) returns JSON lists which unpack the same way.

### 2. Build the relay-side "dumb SCPI relay" node — done
- [x] Implemented `RemoteTextCommandRelayListener` (`src/constellation/relay.py`) as a plain,
      driver-agnostic object with synchronous `write`/`read`/`query`/`connect`/`close`, delegating to an
      internally-held `DirectSCPIRelay`/`VICPDirectSCPIRelay`.
- [x] `examples/osc_networking/scpi_relay_node.py` is the finished, generic node script (works for any
      instrument, `--vicp` flag selects `VICPDirectSCPIRelay`), driven by the TOML config.

### 3. Resolve state broadcast / multi-client observation — done for now
- [x] Implemented `DriverStateBroadcaster` (`src/constellation/networking/labmesh_net.py`): runs a
      `labmesh.RelayAgent` wrapping an already-connected `Driver` in a background thread. Verified
      `Driver.state_to_dict()`-shaped output (with the `metadata` key added) still round-trips correctly
      through `jarnsaxa.from_serial_dict` and is JSON-safe.
- [x] Decided: observer clients also get RPC write-access through the same `RelayAgent` (nothing
      technical prevents it) — concurrent writers are allowed for now, see "Open questions" below.

### 4. Retire the pyfrost-based networking module — done
- [x] Deleted `net_client.py`, `net_server.py`, `network.py` entirely (all three were pyfrost-only).
- [x] `all.py` now imports `constellation.networking.labmesh_net` instead. Also dropped now-orphaned
      `from constellation.networking.net_client import *` lines that several category classes and `ui.py`
      carried but never used anything from.
- [x] Reworked `Identifier`: dropped `remote_addr`/`client_id` entirely (pyfrost-routing-only concept with
      no labmesh equivalent — `address` already doubles as the labmesh `relay_id` when relevant); kept
      `remote_id` as an optional human-friendly nickname, independent of `address`.
- [x] Removed the dead `RemoteInstrument` reference in `src/constellation/ui.py`.
- [x] Deleted the pyfrost-based examples in `examples/to_reformat/` (`client_net_ex1.py`,
      `driver_client_net_ex1.py`, `ipy_term_client_net_ex1.py`, `remote_example1.py`, `server_net_ex1.py`,
      `term_client_net_ex1.py`) — all six imported the now-deleted modules and could not run.

### 5. Adopt labmesh's shared network-password auth (replaces pyfrost accounts)

labmesh has just gained a lightweight auth layer (currently uncommitted in the `../labmesh` working tree —
`labmesh/util.py`'s `network_password()`/`check_password()`/`prompt_network_password()`, wired into
`broker.py`, `relay.py`, `client.py`, and `databank.py`). It's a **single shared secret for the whole mesh**,
read from the `LMH_NETWORK_PASSWORD` env var and attached to every `hello`/`rpc`/`get`/`ingest_start`
message; each node only enforces it if its own `LMH_NETWORK_PASSWORD` is non-empty (opt-in, same pattern as
labmesh's existing CURVE env vars). This is intentionally coarser than pyfrost's per-user accounts — that's
the point, per the decision to drop that complexity.

The check itself lives entirely inside labmesh — Constellation doesn't need to implement any auth logic,
only make sure every node it launches participates correctly:
- [ ] Pin Constellation's `labmesh` dependency to a commit that includes this password system (it must be
      committed in `../labmesh` first — right now it's only a local working-tree diff there; `pyproject.toml`
      here already declares the `labmesh` dependency in anticipation of that).
- [x] The relay-side node script (`scpi_relay_node.py`) calls `prompt_network_password(confirm=True)` at
      startup, mirroring labmesh's own `examples/run_broker.py`.
- [x] `RemoteTextCommandRelayClient.connect()` calls `prompt_network_password()` before connecting.
- [x] Documented (in `README.md`, `docs/pages/what_why_heimdallr.md`, and `examples/osc_networking/README.md`)
      that the whole mesh shares one password, and that it travels in cleartext unless CURVE transport
      encryption (already supported by labmesh's socket helpers) is also enabled.

### 6. Dependencies & packaging — done
- [x] Removed `pyfrost-network` from `pyproject.toml`; added `labmesh >= 0.0.0` and `pyzmq >= 25`.
- [x] Confirmed nothing else in `src/` imports `pyfrost`/`Crypto`/`pycryptodome` now that the old
      networking files are gone.

### 7. Examples & docs — done
- [x] Deleted the superseded v1 (`osc_driver.py`/`osc_client.py`) and broken v2
      (`osc_driver-v2.py`/`osc_client-v2.py`) drafts. `scpi_relay_node.py` (task 2) replaces the relay side;
      `osc_client.py` was rewritten as the owning-client reference (instantiates the real `RigolDS1000Z`
      Driver locally via `RemoteTextCommandRelayClient`, then starts a `DriverStateBroadcaster`).
- [x] Added `osc_client_observer.py` demonstrating the read-only "terminal node" side (subscribes to state,
      never opens its own instrument connection) — makes the multi-client story from task 3 concrete.
- [x] Updated `docs/pages/what_why_heimdallr.md` and the README's "Networking" section to describe the
      labmesh mesh instead of the old pyfrost TCP-server language.
- [x] Added `examples/osc_networking/README.md` with the full run order; the "same driver class locally vs.
      networked" comparison points at the pre-existing `examples/osc_hardware_demo.py`/`osc_dummy_demo.py`
      (identical `RigolDS1000Z(...)` call, just a different `relay=`).

## Remaining before this can be considered fully shipped

- **Commit labmesh's password system** in `../labmesh` (currently just a working-tree diff there) and repoint
  Constellation's dependency at a real commit/tag instead of an implicit sibling-checkout assumption.
- ~~End-to-end smoke test~~ — done: a `DirectoryBroker` + `RelayAgent`-wrapped
  `RemoteTextCommandRelayListener` (backed by a fake in-process relay standing in for real pyvisa hardware,
  since no physical instrument was available) + `RemoteTextCommandRelayClient`, all as separate local
  processes on loopback TCP. Verified: `connect()`/`write()`/`query()`/`read()` round-trip correctly end to
  end with auth disabled; a wrong `LMH_NETWORK_PASSWORD` is cleanly rejected (`connect()` returns `False`,
  logs a 401) rather than hanging or crashing; the correct shared password succeeds. No real instrument
  hardware was exercised — that still needs a hands-on pass with `scpi_relay_node.py` against real SCPI
  hardware before calling this shipped.

## Open questions / risks

- **Concurrent writers (deferred, not overlooked)**: nothing in this design stops two owning clients from
  issuing conflicting `set_*` calls to the same instrument through the dumb relay — concurrent writes are
  explicitly allowed for now. Revisit if this causes real instrument-state corruption or race conditions in
  practice; a future fix would likely be some kind of write-lock/single-owner enforcement at the relay-side
  node (task #2), since that's the one place all writers converge.
- **Verification**: neither `constellation` nor `labmesh` has a test suite; per labmesh's own CLAUDE.md,
  verify changes by actually running the example broker/relay/client/bank scripts end-to-end against real or
  dummy-mode hardware.
