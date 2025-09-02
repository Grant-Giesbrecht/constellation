""" Oscilloscope hardware test
"""

from constellation.all import *
from PyQt6 import QtWidgets
# from constellation.ui import ConstellationWindow
import matplotlib.pyplot as plt
from constellation.instrument_control.oscilloscope.oscilloscope_gui import *
import sys

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.DEBUG

remote_relay = RemoteTextCommandRelayClient()
osc = RigolDS1000Z("TCPIP0::192.168.1.74::INSTR", log=log, relay=)

if not osc.online:
	log.critical(f"Failed to connect to oscilloscope. Exiting")
	sys.exit()

osc.refresh_state()
osc.refresh_data()

osc.dump_state("rigol_state2.state.hdf")

in_dict = hdf_to_dict("rigol_state2.state.hdf")
dict_summary(in_dict, verbose=1)

ins = from_serial_dict(in_dict)
print(ins.state_str())
