from heimdallr.all import *
import matplotlib.pyplot as plt
from jarnsaxa import hdf_to_dict, dict_to_hdf

zva = RohdeSchwarzZVA("TCPIP0::169.254.131.24::INSTR", log)

td_s11 = zva.get_trace_data(1, "Trc5")
td_s22 = zva.get_trace_data(1, "Trc4")
td_s12 = zva.get_trace_data(1, "Trc7")
td_s21 = zva.get_trace_data(1, "Trc6")

zva.write("CALC:PAR:CAT?")
trace_list = zva.inst.read().strip().split(',')

dict_to_hdf({"S11": td_s11, "S22":td_s22, "S12":td_s12, "S21":td_s21}, "zva_capture.hdf")




data = hdf_to_dict("zva_capture.hdf")

plot_vna_mag(data['S11'], label="S11")
plot_vna_mag(data['S22'], label="S22")
plot_vna_mag(data['S21'], label="S21")
plot_vna_mag(data['S12'], label="S12")

plt.legend()
plt.show()