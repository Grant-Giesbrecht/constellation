from heimdallr.all import *
import matplotlib.pyplot as plt

log = plf.LogPile()
log.str_format.show_detail = False
log.terminal_level = plf.LOWDEBUG

osc = RigolDS1000Z("TCPIP0::192.168.0.70::INSTR", log=log, dummy=True)

osc.set_div_time(0.002)
osc.set_offset_time(0.005)
osc.set_div_volt(2, 0.15)

osc.print_state()

print(f"Dummy = {osc.dummy}")
wav1 = osc.get_waveform(1)
wav2 = osc.get_waveform(2)
plt.plot(wav1['time_s'], wav1['volt_V'])
plt.plot(wav2['time_s'], wav2['volt_V'])

plt.grid()
plt.show()