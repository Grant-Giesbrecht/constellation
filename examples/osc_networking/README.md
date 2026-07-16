# Oscilloscope networking example (labmesh)

Demonstrates Constellation's "smart client, dumb relay" networking model (see
`docs/labmesh_migration_plan.md`): the machine wired to the instrument runs a driver-agnostic
relay that only forwards SCPI text, while the real `Driver` (with its state tracking, category
API, and dummy mode) lives wherever the controlling script runs - the same `RigolDS1000Z(...)`
call works locally or networked, only the `relay=` argument changes.

Run each of these in its own terminal, from this directory:

```bash
# 1. Broker - tracks what's connected to the mesh
python osc_broker.py --toml labmesh.toml

# 2. Databank - optional, only needed if a driver script uploads datasets
python osc_bank.py --toml labmesh.toml

# 3. Relay - runs on (or near) the machine physically wired to the oscilloscope
python scpi_relay_node.py TCPIP0::192.168.1.74::INSTR --relay_id osc-1 --toml labmesh.toml

# 4. Owning client - instantiates the real Driver, drives the instrument, and broadcasts its state
python osc_client.py --relay_id osc-1 --toml labmesh.toml

# 5. (optional) Observer client - watches osc_client.py's state broadcast, doesn't own the instrument
python osc_client_observer.py --relay_id osc-1-state --toml labmesh.toml
```

All four/five processes need to agree on the shared mesh password (`LMH_NETWORK_PASSWORD`) -
`osc_broker.py` and `scpi_relay_node.py` will prompt for it interactively if it isn't already set
in the environment; leave it blank to disable auth for local testing.

Concurrent writers to the same instrument are allowed for now - nothing stops a second script from
also getting a `RelayClient` for `osc-1` and calling `set_*` methods directly. See
`docs/labmesh_migration_plan.md`'s open questions.
