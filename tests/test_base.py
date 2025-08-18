from constellation.base import *

def test_bool_conversion():
	assert bool_to_str01(True) == "1"
	assert bool_to_str01(False) == "0"
	
	assert bool_to_ONOFF(True) == "ON"
	assert bool_to_ONOFF(False) == "OFF"
	
def test_s2hms():
	assert s2hms(60) == (0, 1, 0)
	assert s2hms(3600) == (1, 0, 0)
	assert s2hms(1) == (0, 0, 1)

def test_interpret_range():
	
	d1 = {"type": "list", "unit": "dBm", "values": [0]}
	d2 = {"type": "range", "unit": "Hz", "start": 9.8e9, "step": 100e6, "end": 10.2e9}
	d3 = {"type": "range", "unit": "Hz", "start": 0.9e9, "step": 100e6, "end": 1.1e9, "deltas": [-10e6, 10e6]}
	d4 = {"type": "list", "unit": "dBm", "values": [-10, 0, 10, 20, 30]}
	
	assert interpret_range(d1) == [0]
	assert interpret_range(d2) == [9.8e9, 9.9e9, 10e9, 10.1e9, 10.2e9]
	assert interpret_range(d3) == [0.9e9, 0.91e9, 0.99e9, 1e9, 1.01e9, 1.09e9, 1.1e9]
	assert interpret_range(d4) == [-10, 0, 10, 20, 30]