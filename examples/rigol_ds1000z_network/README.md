# Rigol DS1000Z networked measurement example

A complete labmesh network around one Rigol DS1000Z oscilloscope (see
`docs/labmesh_migration_plan.md` for the underlying "smart client, dumb relay" design):

- **`scpi_relay_node.py`** runs on (or near) the machine physically wired to the scope. It's a
  driver-agnostic "dumb" relay - it only forwards raw SCPI text and knows nothing about
  oscilloscopes, categories, or state.
- **`controller_client.py`** is the client that actually instructs the scope what to measure: it
  instantiates the real `RigolDS1000Z` Driver, configures the timebase/channels/trigger, and then
  periodically captures a waveform and uploads it to the databank node. It also broadcasts its own
  Driver state so the monitor can watch without controlling the instrument.
- **`monitor_client.py`** is a second, read-only client: it never connects to the scope itself, it
  only subscribes to the controller's state broadcast and to dataset-upload notifications
  (downloading each new waveform from the databank to confirm it arrived).
- **`databank.py`** stores every waveform `controller_client.py` uploads and serves it back out.
- **`broker.py`** tracks what's connected to the mesh - it's the only thing every other node talks
  to directly.

## Run order

Each in its own terminal, from this directory:

```bash
# 1. Broker
python broker.py --toml labmesh.toml

# 2. Databank
python databank.py --toml labmesh.toml

# 3. Relay - point this at your scope's real VISA address
python scpi_relay_node.py TCPIP0::192.168.1.74::INSTR --relay_id rigol-1 --toml labmesh.toml

# 4. Controller - configures the scope and starts uploading waveforms every 10s
python controller_client.py --relay_id rigol-1 --channels 1,2 --interval 10 --toml labmesh.toml

# 5. Monitor - watches, doesn't control
python monitor_client.py --relay_id rigol-1-state --toml labmesh.toml
```

All nodes need to agree on the shared mesh password (`LMH_NETWORK_PASSWORD`) - `broker.py`,
`scpi_relay_node.py`, `controller_client.py`, and `monitor_client.py` will each prompt for it
interactively if it isn't already set in the environment; leave it blank to disable auth for local
testing.

Waveforms land in `./bank_data/` (as raw JSON bytes: `relay_id`, `channel`, `captured_at`, and the
`waveform` dict itself) and get mirrored into `./monitor_downloads/` by the monitor as they arrive.

Concurrent writers to the scope are allowed for now - nothing stops a second script from also
getting a `RelayClient` for `rigol-1` and calling `set_*` methods directly. See
`docs/labmesh_migration_plan.md`'s open questions.

## Note on `controller_client.py`'s Driver usage

Instantiating and driving the oscilloscope here is identical to local, non-networked use (compare
with `examples/osc_hardware_demo.py`) - the only difference is passing a
`RemoteTextCommandRelayClient` instead of the default `DirectSCPIRelay`, and treating `address` as
a labmesh `relay_id` instead of a VISA resource string.
