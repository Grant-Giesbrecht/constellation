import numpy as np
import os
from jarnsaxa import hdf_to_dict

def lin_to_dB(x_lin:float, use10:bool=False):
	if use10:
		return 10*np.log10(x_lin)
	else:
		return 20*np.log10(x_lin)

def has_ext(path, exts):
	return os.path.splitext(path)[1].lower() in [e.lower() for e in exts]

def bounded_interp(x, y, x_target):
	if x_target < x[0] or x_target > x[-1]:
		return None
	return np.interp(x_target, x, y)
 
def format_sparam(data:list, format):
	''' Expects data in complex format, returns formatted data.
	
	format options:
	 - complex
	 - logmag (dB-20)
	 - linmag
	 - phase (degrees)
	 - real
	 - imag
	'''
	
	format_lower = format.lower()
	
	if format_lower == "complex":
		return data
	elif format_lower == "logmag":
		return lin_to_dB(np.abs(data))
	elif format_lower == "linmag":
		return np.abs(data)
	elif format_lower == "phase":
		return np.angle(data, deg=True)
	elif format_lower == "real":
		return np.real(data)
	elif format_lower == "imag":
		return np.imag(data)
	else:
		ValueError(f"Unrecognized format type {format}.")

class SParams:
	
	def __init__(self):
		self.s_parameters = {} # internally saves data as np.complex128
		self.metadata = {} # optional metadata
		
		# self.universal_freqs = True
		self.frequencies = {}
	
	def load(self, filename:str):
		''' Loads a file into the specified file. '''
		
		recognized_parameters = ["S11", "S21", "S12", "S22"]
		
		if has_ext(filename, [".hdf", ".h5", "hdf5", ".sparam"]):
			''' Expects HDF to define S11 S21 S12 and S22 (or fewer), each should
			have an `x` and `y` value (y is complex s-parameter with phase) and `x`
			is frequency.
			'''
			
			try:
				
				# Load s-parameter data
				data_full = hdf_to_dict(filename)
				
				# Read S-parameter data and check for older format with no metadata
				if 'data' in data_full.keys():
					data = data_full['data']
					if 'info' in data_full.keys() and isinstance(data_full['info'], dict):
						self.metadata = data_full['info']
				else:
					data = data_full
				
				# Populate result
				for param in data.keys():
					
					if param in recognized_parameters:
						self.s_parameters[param] = data[param]['y']
						self.frequencies[param] = data[param]['x']	
				
			except Exception as e:
				raise ValueError(f"Failed to load file {filename}. ({e})")
				
				
			
		elif has_ext(filename, [".csv"]):
			pass
		elif has_ext(filename, [".s2p", ".snp", ".s1p"]):
			pass
	
	def get_parameter(self, param:str, freq:float=None, format:str="logmag"):
		''' Returns the specified S-parameter, either in a list at all defined frequnecy points, or at
		a specific frequency if arg `freq` is not None.
		
		format options:
		 - complex
		 - logmag
		 - linmag
		'''
		
		# Verify that S11 has been populated
		if param not in self.s_parameters:
			raise AttributeError(f"{param} has not been populated.")
		
		# Return requested value
		if freq is None:
			return format_sparam(self.s_parameters[param], format=format)
		else:
			return format_sparam( bounded_interp(self.get_parameter(param), self.get_freqeuncy(param), freq), format=format)
		
	def get_frequency(self, param:str="S11"):
		''' Returns the frequency for the selected parameter.'''
		
		# if self.universal_freqs:
		# 	return self.frequencies["univ"]
		# elif param is None:
		# 	AttributeError(f"Frequency changes for different S-parameters, get_frequency requires param to be defined.")
		# else:
			
		# Verify that S11 has been populated
		if param not in self.frequencies:
			raise AttributeError(f"{param} has not been populated.")
		
		return self.frequencies[param]
	
	def S11(self, freq=None, format:str='logmag'):
		''' Returns S11, either in a list at all defined frequnecy points, or at
		a specific frequency if arg `freq` is not None.
		'''
		return self.get_parameter("S11", freq=freq, format=format)
		
	def S11_freq(self):
		''' Returns the frequency for S11.'''
		
		return self.get_frequency(param="S11")
	
	def S22(self, freq=None, format:str='logmag'):
		''' Returns S22, either in a list at all defined frequnecy points, or at
		a specific frequency if arg `freq` is not None.
		'''
		return self.get_parameter("S22", freq=freq, format=format)
		
	def S22_freq(self):
		''' Returns the frequency for S11.'''
		
		return self.get_frequency(param="S22")
	
	def S21(self, freq=None, format:str='logmag'):
		''' Returns S21, either in a list at all defined frequnecy points, or at
		a specific frequency if arg `freq` is not None.
		'''
		return self.get_parameter("S21", freq=freq)
		
	def S21_freq(self):
		''' Returns the frequency for S21.'''
		
		return self.get_frequency(param="S21")
	
	def S12(self, freq=None, format:str='logmag'):
		''' Returns S12, either in a list at all defined frequnecy points, or at
		a specific frequency if arg `freq` is not None.
		'''
		return self.get_parameter("S12", freq=freq, format=format)
		
	def S12_freq(self):
		''' Returns the frequency for S12.'''
		
		return self.get_frequency(param="S12")
	
