''' QuantAsylum QA403 through QA40x-rs (or the official QA40x application).

Start the application with the analyzer attached first - its REST server listens on
http://localhost:9402.

WARNING: every capture plays the enabled generators on the analyzer's outputs. Check what is
connected before running this.
'''

import sys

from constellation.all import *

log = plf.LogPile()
log.set_terminal_level("INFO")

qa = QuantAsylumQA403("http://localhost:9402", log)
if not qa.online:
	print(qa.connection_summary()["diagnosis"])
	sys.exit(1)

# Settings cannot be read back from the analyzer, so start from a known configuration: preset()
# resets it and switches both generators off.
qa.preset()
qa.set_sample_rate(48000)
qa.set_input_range(18)
qa.set_generator_freq(1, 1000)
qa.set_generator_amplitude(1, -10)
qa.set_generator_enable(1, True)

# Several measurements per capture: trigger manually, and the getters read that capture.
qa.set_trigger_policy(QuantAsylumQA403.TRIGGER_MANUAL)

for i in range(5):
	qa.send_manual_trigger()
	thd = qa.get_thd("left", 1000, 20000)
	level = qa.get_peak_level("left", 950, 1050)
	print(f"Capture {i}: 1 kHz at {level:.2f} dBV, THD {thd:.2f} dB")

qa.set_generator_enable(1, False)
