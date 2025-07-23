from heimdallr.base import *

def lin_to_dB(x_lin:float, use10:bool=False):
	if use10:
		return 10*np.log10(x_lin)
	else:
		return 20*np.log10(x_lin)

def dB_to_lin(x_dB:float, use10:bool=False):
	if use10:
		return np.power(10, (np.array(x_dB)/10))
	else:
		return np.power(10, (np.array(x_dB)/20))