""" Pop up the arbitrary waveform generator GUI (see
src/constellation/instrument_control/arb_waveform_generator/arb_waveform_generator_gui.py and
docs/gui_authoring_guide.md), either against real hardware or in --dummy mode.

Run:
  python awg_gui_demo.py --dummy
  python awg_gui_demo.py --driver keysight --resource "TCPIP0::192.168.1.91::INSTR"
  python awg_gui_demo.py --driver siglent  --resource "TCPIP0::192.168.1.90::INSTR" --tabs
  python awg_gui_demo.py --dummy --full      # every control in the full view (--compact for compact)
"""

import argparse
import sys
from constellation.all import *
from PyQt6 import QtWidgets

DRIVERS = {"keysight": Keysight33500, "siglent": SiglentSDG2000X}

parser = argparse.ArgumentParser()
parser.add_argument("--dummy", action="store_true", help="Run without hardware attached, using a simulated instrument.")
parser.add_argument("--driver", choices=sorted(DRIVERS), default="keysight", help="Which AWG driver to use.")
parser.add_argument("--resource", default="TCPIP0::192.168.1.91::INSTR", help="VISA resource string. Ignored with --dummy.")
parser.add_argument("--tabs", action="store_true", help="Start with the channels in tabs rather than side by side.")
add_view_arguments(parser)
args = parser.parse_args()

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.INFO

awg = DRIVERS[args.driver](args.resource, log, dummy=args.dummy)

if args.tabs:
	ArbitraryWaveformGeneratorWidget.DEFAULT_CHANNEL_LAYOUT = LAYOUT_TABS

app = QtWidgets.QApplication(sys.argv)
app.setStyle("Fusion")

main_window = ConstellationWindow(log, parameter_view=view_from_arguments(args))
main_window.add_instrument(driver=awg, title="Waveform Generator" + (" (dummy)" if args.dummy else ""))
main_window.setWindowTitle("Waveform Generator GUI Demo")
main_window.resize(1200, 600)
main_window.show()

app.exec()
