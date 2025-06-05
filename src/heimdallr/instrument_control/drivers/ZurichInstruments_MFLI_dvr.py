''' Driver for Zurich Instruments MFLI Lock-In Amplifier

Manual: 
'''

from heimdallr.base import *
from heimdallr.instrument_control.categories.lock_in_amplifier_ctg import *
import zhinst.utils

def bool_to_int(x:bool):
	if x:
		return 1
	else:
		return 0

class ZurichInstrumentsMFLI(LockInAmplifierCtg):
	
	def __init__(self, dev_sn:str, address:str, log:plf.LogPile):
		super().__init__(address, log, is_scpi=False, expected_idn="MFLI")
		
		self.zhinst_params = None
		self.dev_sn = dev_sn
		
		self.range_options = np.array([0.01, 0.1, 1, 10])
	
	def connect(self, check_id:bool=True):
		
		try:
			self.inst, _, self.zhinst_params = zhinst.utils.create_api_session('dev5652', 6, '192.168.88.82')
			self.online = True
			
			try:
				self.id.idn_model = self.zhinst_params['devicetype']
			except:
				self.id.idn_model = None
				
			if check_id:
				self.query_id()
			
		except Exception as e:
			self.log.error(f"Failed to connect to address: {self.address}. ({e})")
			self.online = False
	
	def query_id(self):
		''' Checks the IDN of the instrument, and makes sure it matches up.'''
		
		# Instead of querying the id_model here, it's done only when 
		# the instrument is opened.
		
		#TODO: Add some trivial read here to make sure the instrument is still online
		
		if self.id.idn_model is not None:
			self.online = True
			self.log.debug(f"Instrument connection state: >ONLINE<")
			
			if self.expected_idn is None or self.expected_idn == "":
				self.log.debug("Cannot verify hardware. No verification string provided.")
				return
			
			# Check if model is right
			if self.expected_idn.upper() in self.id.idn_model.upper():
				self.verified_hardware = True
				self.log.debug(f"Hardware verification >PASSED<", detail=f"Received string: {self.id.idn_model}")
			else:
				self.verified_hardware = False
				self.log.debug(f"Hardware verification >FAILED<", detail=f"Received string: {self.id.idn_model}")
		else: # TODO: This isnt currently going to work unless I re-quety the device model
			self.log.debug(f"Instrument connection state: >OFFLINE<")
			self.online = False
	
	def set_offset(self, offset_V:float):
		''' Sets the offset of the signal output '''
		self.log.debug(f"MFLI Driver setting offset to voltage: {offset_V} V")
		self.inst.setDouble(f'/{self.dev_sn}/sigouts/0/offset', offset_V)
	
	def set_output_enable(self, enable:bool):
		''' Enables the output '''
		self.inst.setInt(f'/{self.dev_sn}/sigouts/0/on', bool_to_int(enable))
	
	def set_50ohm(self, enable:bool):
		''' Sets 50ohm mode for the output port '''
		self.inst.setInt(f'/{self.dev_sn}/sigouts/0/imp50', bool_to_int(enable))
	
	def set_autorange(self, enable:bool):
		''' Autorange the output port '''
		self.inst.setInt(f'/{self.dev_sn}/sigouts/0/autorange', bool_to_int(enable))
	
	def set_range(self, range_V:float):
		''' Sets the range of the output port. Will automatically change
		range to one of the four acceptable options.  '''
		
		# Find if in 50 ohm mode
		if self.inst.getInt('/dev5652/sigouts/0/imp50') == 1:
			range_options_scal = self.range_options/2
		else:
			range_options_scal = self.range_options
			
		# Set to autorange options
		if range_V not in range_options_scal:
			
			# Return closest number (Thanks copilot)
			closest_values = [x for x in range_options_scal if x >= range_V]
			if closest_values:
				range_V = min(closest_values, key=lambda x: abs(x - range_V))
			else:
				
				# Otherwise just return largest
				range_V = range_options_scal[-1]
		
		# Set range
		self.inst.setDouble(f'/{self.dev_sn}/sigouts/0/range', range_V)
	
	def set_output_ac_enable(self, enable:bool):
		''' Enable the AC component of the output port '''
		self.inst.setInt(f'/{self.dev_sn}/sigouts/0/enables/1', bool_to_int(enable))
	
	def set_differential_enable(self, enable:bool):
		''' Enable the differentail mode of the output port '''
		self.inst.setInt(f'/{self.dev_sn}/sigouts/0/diff', bool_to_int(enable))
		
	def set_output_ac_ampl(self, vpk_V:float):
		''' Sets the offset of the signal output. Will NOT adjust autorange, however the offset function will. '''
		self.inst.setDouble(f'/{self.dev_sn}/sigouts/0/amplitudes/1', vpk_V)