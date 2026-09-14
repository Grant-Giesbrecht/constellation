''' Audio analyzer in dummy mode: configure a tone and measure it. No hardware or QA40x application
needed. '''

import matplotlib.pyplot as plt

from constellation.all import *

log = plf.LogPile()

qa = QuantAsylumQA403("http://localhost:9402", log, dummy=True)

qa.set_generator_freq(1, 1000)
qa.set_generator_amplitude(1, -10)
qa.set_generator_enable(1, True)

# By default every measurement captures first, so each line below is its own capture.
print(f"RMS level (20 Hz - 20 kHz): {qa.get_rms_level('left'):.2f} dBV")
print(f"THD:                        {qa.get_thd('left', 1000, 20000):.2f} dB")
print(f"Peak level 950-1050 Hz:     {qa.get_peak_level('left', 950, 1050):.2f} dBV")

spectrum = qa.get_spectrum("left")
plt.semilogx(spectrum["x"][1:], spectrum["y"][1:])
plt.xlabel(f"Frequency ({spectrum['x_units']})")
plt.ylabel(f"Level ({spectrum['y_units']})")
plt.grid(True, which="both", alpha=0.3)
plt.show()
