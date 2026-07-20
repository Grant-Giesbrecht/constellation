""" Minimal example: pop up the power supply GUI (see
src/constellation/instrument_control/power_supply/power_supply_gui.py and
docs/gui_authoring_guide.md), either against real hardware or in --dummy mode.

Run:
  python psu_gui_demo.py --dummy
  python psu_gui_demo.py --resource "TCPIP0::192.168.1.238::INSTR"
"""

import argparse
import sys
from constellation.all import *
from PyQt6 import QtWidgets

parser = argparse.ArgumentParser()
parser.add_argument("--dummy", action="store_true", help="Run without physical PSU hardware attached, using simulated dummy responses.")
parser.add_argument("--resource", default="TCPIP0::192.168.1.238::INSTR", help="VISA resource string for the PSU. Ignored with --dummy.")
args = parser.parse_args()

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.DEBUG

psu = RigolDP832(args.resource, log, dummy=args.dummy)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")

main_window = ConstellationWindow(log)
main_window.add_instrument(driver=psu, title="Power Supply" + (" (dummy)" if args.dummy else ""))
main_window.setWindowTitle("Power Supply GUI Demo")
main_window.resize(700, 400)
main_window.show()

app.exec()
