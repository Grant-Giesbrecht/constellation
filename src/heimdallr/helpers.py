from heimdallr.base import *

def lin_to_dB(x_lin:float, use10:bool=False):
	if use10:
		return 10*np.log10(x_lin)
	else:
		return 20*np.log10(x_lin)

def dB_to_lin(x_dB:float, use10:bool=False):
	if use10:
		return np.power(10, (x_dB/10))
	else:
		return np.power(10, (x_dB/20))

def plot_vna_mag(data:dict, label:str=""):
	''' Helper function to plot the data output from a VNA get_trace_data() call.
	
	Args:
		data (dict): VNA trace data to plot
		label (str): Optional label for data
	
	Returns:
		None
	'''
	plt.plot(np.array(data['x'])/1e9, lin_to_dB(np.abs(data['y'])), label=label)
	
	plt.grid(True)
	plt.xlabel("Frequency [GHz]")
	plt.ylabel("S-Parameters [dB]")