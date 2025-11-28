from constellation.instrument_control.digital_multimeter.digital_multimeter_ctg import *
from constellation.instrument_control.digital_multimeter.drivers.Siglent_SDM3000X_dvr import *

log = plf.LogPile()

dmm = SiglentSDM3000X("TCPIP0::192.168.1.30::INSTR", log, dummy=True)

