""" The purpose of this example is to show how to make a basic
oscilloscope GUI.
"""

from heimdallr.all import *
from PyQt6 import QtWidgets
# from heimdallr.ui import HeimdallrWindow
import matplotlib.pyplot as plt
from heimdallr.instrument_control.oscilloscope.oscilloscope_gui import *

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.LOWDEBUG

osc = RigolDS1000Z("TCPIP0::192.168.0.70::INSTR", log=log, dummy=True)






#================= Basic PyQt App creation things =========================

# Create app object
app = QtWidgets.QApplication(sys.argv)
app.setStyle(f"Fusion")

main_window = HeimdallrWindow(log)
osc_widget = BasicOscilloscopeWidget(main_window, osc, log)
main_window.setCentralWidget(osc_widget)
main_window.setWindowTitle("Oscilloscope")
main_window.show()

osc.set_div_time(0.001)
osc.set_offset_time(0.002)
osc.set_chan_enable(2, False)
osc.set_chan_enable(3, False)
osc.print_state()

app.exec()