from heimdallr.all import *

log = plf.LogPile()
log.str_format.show_detail = True
log.terminal_level = plf.LOWDEBUG

osc = RigolDS1000Z("TCPIP0::192.168.0.70::INSTR", log=log, dummy=True)