"""
Saves S-parameters from a VNA to disk.
"""


from constellation.all import *
import matplotlib.pyplot as plt
from jarnsaxa import hdf_to_dict, dict_to_hdf
from constellation.instrument_control.vector_network_analyzer.drivers.RohdeSchwarz_ZVA_dvr import *



FILENAME = input("Filename:")
cal_notes = input("Calibration notes:")
other_notes = input("Other notes?:")

zva = RohdeSchwarzZVA("TCPIP0::169.254.131.24::INSTR", log)


td_s11 = zva.get_trace_data(1, "Trc2")
td_s22 = zva.get_trace_data(1, "Trc4")
td_s12 = zva.get_trace_data(1, "Trc1")
td_s21 = zva.get_trace_data(1, "Trc3")

zva.write("CALC:PAR:CAT?")
trace_list = zva.inst.read().strip().split(',')

dict_to_hdf({"data":{"S11": td_s11, "S22":td_s22, "S12":td_s12, "S21":td_s21}, "info":{"cal_notes":cal_notes, "gen_notes":other_notes, "timestamp":datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')}}, FILENAME)


all_data = hdf_to_dict(FILENAME)
data = all_data['data']

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

plt.plot(data['S11']['x'], Sx1L, label="Sn1L")
plt.plot(data['S22']['x'], Sx2L, label="Sn2L")

plt.xlabel("Frequency (GHz)")
plt.ylabel("S-Parameter (dB)")
plt.grid(True)
plt.legend()

plt.show()