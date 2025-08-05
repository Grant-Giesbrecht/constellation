from heimdallr.all import *
import sys

# Create log object
log = plf.LogPile()

# Create NRX Driver
nrx = RohdeSchwarzNRX("TCPIP0::192.168.0.10::INSTR", log)

# Preset device
nrx.preset()

# Get meas frequency
nrx.set_meas_frequency(1e9)
fmeas = nrx.get_meas_frequency()
print(f"Read meas freq = {fmeas/1e9} GHz")

# Set averaging characteristics
nrx.set_averaging_count(500)

# Send trigger and wait for completion
nrx.send_trigger()
if not nrx.wait_ready(timeout_s=2):
	log.critical(f"Timeout occured during read of NRX power measurement.")
	sys.exit()

# Get value
data = nrx.get_measurement()
print(f"Measured value: {data} dBm")