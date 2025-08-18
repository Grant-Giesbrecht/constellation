from constellation.all import *

log = plf.LogPile()

# sa = SiglentSSA3000X("TCPIP0::192.168.0.10::INSTR", log)
sa = RohdeSchwarzFSQ("TCPIP0::192.168.1.14::INSTR", log)