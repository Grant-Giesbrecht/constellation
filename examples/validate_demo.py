from constellation.instrument_control.all import *

log = plf.LogPile()
log.set_terminal_level("LOWDEBUG")

sa = SiglentSSA3000X("GPIB::17::INTR", log, dummy=True)