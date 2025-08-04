from heimdallr.all import *
import matplotlib.pyplot as plt
from jarnsaxa import hdf_to_dict, dict_to_hdf

FILENAME = "SParameters_XLD_Chip_LongTrace_23July2025.hdf"

zva = RohdeSchwarzZVA("TCPIP0::169.254.131.24::INSTR", log)

td_s11 = zva.get_trace_data(1, "Trc2")
td_s22 = zva.get_trace_data(1, "Trc4")
td_s12 = zva.get_trace_data(1, "Trc1")
td_s21 = zva.get_trace_data(1, "Trc3")

zva.write("CALC:PAR:CAT?")
trace_list = zva.inst.read().strip().split(',')

dict_to_hdf({"S11": td_s11, "S22":td_s22, "S12":td_s12, "S21":td_s21}, FILENAME)


data = hdf_to_dict(FILENAME)

plot_vna_mag(data['S11'], label="S11")
plot_vna_mag(data['S22'], label="S22")
plot_vna_mag(data['S21'], label="S21")
plot_vna_mag(data['S12'], label="S12")

plt.legend()

use10 = True

S11L = np.abs(data['S11']['y']) # dB_to_lin(data['S11']['y'], use10)
S21L = np.abs(data['S21']['y']) #dB_to_lin(data['S21']['y'], use10)
S22L = np.abs(data['S22']['y']) #dB_to_lin(data['S22']['y'], use10)
S12L = np.abs(data['S12']['y']) #dB_to_lin(data['S12']['y'], use10)

Sx1L = S11L + S21L
Sx2L = S22L + S12L

plt.figure(2)
# plt.plot(data['S11']['x'], lin_to_dB(Sx1L, use10), label="Sn1")
# plt.plot(data['S22']['x'], lin_to_dB(Sx2L, use10), label="Sn2")

plt.plot(data['S11']['x'], Sx1L, label="Sn1L")
plt.plot(data['S22']['x'], Sx2L, label="Sn2L")

# plt.plot(data['S11']['x'], S11L, label="S11L")
# plt.plot(data['S21']['x'], S21L, label="S21L")
# plt.plot(data['S12']['x'], S12L, label="S12L")
# plt.plot(data['S22']['x'], S22L, label="S22L")
plt.xlabel("Frequency (GHz)")
plt.ylabel("S-Parameter (dB)")
plt.grid(True)
plt.legend()

plt.show()