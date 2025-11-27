from constellation.instrument_control.power_supply.power_supply_ctg import *
from constellation.instrument_control.power_supply.drivers.Rigol_DP832_dvr import *

log = plf.LogPile()

psu = RigolDP832("TCPIP0::192.168.1.238::INSTR", log, dummy=True)

psu.print_state()

# Channel 2 set to output 15 volts
psu.set_voltage(2, 15)

# Printing the state will show 15 volts out, but the measurement wont be updated until you call get_measured_output or refresh_state():
psu.print_state() # <-- this will have out of date measurements.

psu.refresh_state()
psu.print_state() # <-- this will be a happy and sensible output 