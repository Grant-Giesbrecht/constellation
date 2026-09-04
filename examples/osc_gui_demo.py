""" Minimal example: pop up the oscilloscope GUI (see
src/constellation/instrument_control/oscilloscope/oscilloscope_gui.py and
docs/gui_authoring_guide.md), either against real hardware or in --dummy mode.

Run:
  python osc_gui_demo.py --dummy
  python osc_gui_demo.py --resource "TCPIP0::192.168.1.74::INSTR"

Note `add_instrument(driver=...)` rather than constructing the widget directly: the window builds
the InstrumentBridge that keeps instrument I/O off the Qt thread, finds the widget registered for
the driver's category, and docks the result. A widget never receives a Driver.
"""

import argparse
import sys

from PyQt6 import QtWidgets

from constellation.all import *
from constellation.instrument_control.oscilloscope.oscilloscope_gui import *

parser = argparse.ArgumentParser()
parser.add_argument("--dummy", action="store_true", help="Run without physical oscilloscope hardware attached, using simulated dummy responses.")
parser.add_argument("--resource", default="TCPIP0::192.168.1.74::INSTR", help="VISA resource string for the scope. Ignored with --dummy.")
args = parser.parse_args()

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.INFO

osc = RigolDS1000Z(args.resource, log=log, dummy=args.dummy)

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")

main_window = ConstellationWindow(log)
main_window.add_instrument(driver=osc, title="Oscilloscope" + (" (dummy)" if args.dummy else ""))
main_window.setWindowTitle("Oscilloscope GUI Demo")
main_window.resize(1000, 650)
main_window.show()

app.exec()
