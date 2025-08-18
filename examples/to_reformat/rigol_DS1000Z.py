from constellation.all import *


osc = RigolDS1000Z("TCPIP0::192.168.1.20::INSTR", log)
osc.refresh_state()
