from constellation.instrument_control.all import *

log = plf.LogPile()

psu = RigolDP832("TCPIP::192.168.1.238::INSTR", log)
