from heimdallr.base import *

def test_bool_conversion():
	assert bool_to_str01(True) == "1"
	assert bool_to_str01(False) == "0"
	
	assert bool_to_ONOFF(True) == "ON"
	assert bool_to_ONOFF(False) == "OFF"
	
	