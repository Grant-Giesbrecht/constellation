from constellation.instrument_control.all import *

log = plf.LogPile()

sa = RohdeSchwarzFSE("GPIB::17::INTR", log, dummy=True)