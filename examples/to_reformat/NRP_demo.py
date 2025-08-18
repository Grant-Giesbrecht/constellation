from constellation.all import *
import sys

log = plf.LogPile()

address = "USB0::0x0AAD::0x0139::101706::INSTR"

nrp = RohdeSchwarzNRP(address, log)
if not nrp.online:
	log.critical(f"Failde to connect to address >:a{address}<")
	sys.exit()

# Preset
print("Presetting insturment.")
nrp.preset()

# Change frequency
print(f"\tPost-preset freq: {nrp.get_meas_frequency()/1e9} GHz")
nrp.set_meas_frequency(2e9)
print(f"\tChanged freq to: {nrp.get_meas_frequency()/1e9} GHz")

#Try to read value
print(f"\nTriggering...")
nrp.send_trigger(wait=True)
print(f"\tMeasurement value: {nrp.get_measurement()} dBm.")
