from constellation.all import *

zva = RohdeSchwarzZVA("TCPIP0::169.254.131.24::INSTR", log)

zva.clear_traces()

zva.add_trace(1, "Trc1", BasicVectorNetworkAnalyzerCtg.MEAS_S11)
zva.add_trace(1, "Trc2", BasicVectorNetworkAnalyzerCtg.MEAS_S12)
zva.add_trace(1, "Trc3", BasicVectorNetworkAnalyzerCtg.MEAS_S21)
zva.add_trace(1, "Trc4", BasicVectorNetworkAnalyzerCtg.MEAS_S22)

# zva.set_rf_power(-40)
# zva.set_rf_enable(True)

zva.refresh_channels_and_traces()

