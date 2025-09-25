"""
Saves S-parameters from a VNA to disk.
"""


from constellation.all import *
import matplotlib.pyplot as plt
from jarnsaxa import hdf_to_dict, dict_to_hdf
from constellation.instrument_control.vector_network_analyzer.drivers.RohdeSchwarz_ZVA_dvr import *
import sys

FILENAME = input("Filename:")
cal_notes = input("Calibration notes:")
other_notes = input("Other notes?:")

zva = RohdeSchwarzZVA("TCPIP0::169.254.131.24::INSTR", log)

zva.refresh_channels_and_traces()

# Find trace names for each measurement
trc_s11 = zva.find_trace(BasicVectorNetworkAnalyzerCtg.MEAS_S11)
trc_s12 = zva.find_trace(BasicVectorNetworkAnalyzerCtg.MEAS_S12)
trc_s21 = zva.find_trace(BasicVectorNetworkAnalyzerCtg.MEAS_S21)
trc_s22 = zva.find_trace(BasicVectorNetworkAnalyzerCtg.MEAS_S22)

# Check if any required traces were not found
if None in [trc_s11, trc_s12, trc_s21, trc_s22]:
	print(f"Failed to find one or more required traces. Aborting.")
	sys.exit()
	
# Read trace data
td_s11 = zva.get_trace_data(trc_s11)
td_s22 = zva.get_trace_data(trc_s22)
td_s12 = zva.get_trace_data(trc_s12)
td_s21 = zva.get_trace_data(trc_s21)

# Format data into dictionary
state_data = zva.state_to_dict()
sp_data = {"S11": td_s11, "S22":td_s22, "S12":td_s12, "S21":td_s21}
file_info = {"cal_notes":cal_notes, "gen_notes":other_notes, "timestamp":datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f'), "instrument_state":state_data}
data_out = {"data":sp_data, "info":file_info}

# Save to disk
dict_to_hdf(data_out, FILENAME)

# Create S-parameter plot
plot_vna_mag(sp_data['S11'], label="S11")
plot_vna_mag(sp_data['S22'], label="S22")
plot_vna_mag(sp_data['S21'], label="S21")
plot_vna_mag(sp_data['S12'], label="S12")


plt.legend()
plt.show()