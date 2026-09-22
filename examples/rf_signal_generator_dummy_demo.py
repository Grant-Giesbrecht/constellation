''' Drives an HP/Agilent 8371xB CW source with no hardware attached.

Shows the two things that are specific to this category: a level is a power in dBm rather than an
amplitude, and this family genuinely cannot set a phase - so that call raises instead of quietly
recording a value the instrument never received.
'''

from constellation.instrument_control.rf_signal_generator.rf_signal_generator_ctg import *
from constellation.instrument_control.rf_signal_generator.drivers.Agilent_837xxB_dvr import *

log = plf.LogPile()

src = Agilent837xxB("GPIB0::19::INSTR", log, dummy=True)

src.set_reference_source(RFSignalGenerator.REF_EXTERNAL)

# -1.4 dB of cable loss between the front panel and the device under test. Applied before the
# level, since it changes what the level means.
src.set_power_offset(1, -1.4)
src.set_frequency(1, 10e9)
src.set_power(1, -12)
src.set_output_enable(1, True)

print(f"Reference : {src.get_reference_source()}")
print(f"Frequency : {src.get_frequency(1)/1e9} GHz")
print(f"Level     : {src.get_power(1)} dBm (offset {src.get_power_offset(1)} dB)")
print(f"RF output : {'ON' if src.get_output_enable(1) else 'OFF'}")

# What this instrument cannot do, asked before calling rather than caught afterwards.
print(f"Unavailable: {src.unavailable_features()}")

try:
	src.set_phase(1, 90)
except FeatureUnavailable as e:
	print(f"As expected: {e}")
