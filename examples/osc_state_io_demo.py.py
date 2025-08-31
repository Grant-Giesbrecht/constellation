""" The purpose of this example is to show how dummy mode works. Notice
how the waveform is trimmed for the channel with the finer voltage
resultion because the dummy waveform is mimicing clipping in the real
hardware.
"""

from constellation.all import *
import matplotlib.pyplot as plt
from ganymede import dict_summary
from colorama import Fore, Style

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.LOWDEBUG

osc = RigolDS1000Z("TCPIP0::192.168.0.70::INSTR", log=log, dummy=True)
osc2 = RigolDS1000Z("TCPIP0::192.168.0.71::INSTR", log=log, dummy=True)

osc.set_div_time(0.002)
osc.set_offset_time(0.005)
osc.set_div_volt(2, 0.15)

print(f"{Style.RESET_ALL}=============== Print State ========================")
osc.print_state()

print(f"{Style.RESET_ALL}\n========== State -> Dict: Summary ==================")
sd = osc.state_to_dict()
dict_summary(sd, verbose=1)

print("Loaded state into osc2")
print(f"{Style.RESET_ALL}=============== Osc2: Print State ========================")
osc2.load_state_dict(sd)
osc2.print_state()

print(f"{Style.RESET_ALL}\n========== Osc2: State -> Dict: Summary ==================")
sd2 = osc2.state_to_dict()
dict_summary(sd2, verbose=1)

# print("\n========== State -> Dict: Summary ==================")
# sd = osc.state_to_dict()

# print(f"Dummy = {osc.dummy}")
# wav1 = osc.get_waveform(1)
# wav2 = osc.get_waveform(2)
# plt.plot(wav1['time_s'], wav1['volt_V'])
# plt.plot(wav2['time_s'], wav2['volt_V'])

# plt.grid()
# plt.show()