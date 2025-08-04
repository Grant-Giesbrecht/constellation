from heimdallr.all import *

log = plf.LogPile()

vna = KeysightPNAE8364B("TCPIP0::10.10.71.10::INSTR", log)

vna.write(f"SENS:SWE:MODE HOLD")
vna.write(f"CALC:PAR:DEL:ALL")
vna.add_trace(1, 1, VectorNetworkAnalyzerCtg.MEAS_S11)
vna.write(f"SENS:SWE:POIN 11")
vna.send_manual_trigger()
print(vna.get_trace_data(1, 1))