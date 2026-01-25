import random
from constellation.base import *
import time
import numpy as np
import pylogfile.base as plf

import stardust.algorithm as staral
from stardust.cli import rde

HIGHDEBUG = 15

def check_online(dvr:Driver, name:str, log:plf.LogPile):
	if dvr.online:
		log.info(f"{name} >ONLINE<.")
	else:
		log.info(f"Failed to connect to {name}.")
		exit()

def is_within_tol(target:float, value:float, tol:float):
	''' Checks if target and value are within 'tol' of eachother
	'''
	return (value <= target+tol) and (value >= target-tol)

class MonotonicNonlinearIterationPoint:
	
	def __init__(self, set_point:float, slope:float, x0:float, tol:float, y0:float, x_low:float, x_high:float, y_low:float, y_high:float, x_prime:float, probe_dx_prime:float, tol_prime:float, error_msg:str=None):
		
		self.set_point = set_point
		self.slope = slope
		self.x0 = x0
		

#TODO: Record all points and avoid initial slope measurement if it's been measured before.
class MonotonicNonlinearController:
	
	def __init__(self, log:plf.LogPile, max_step:float, default_probe_dx:float, backup_slope_tests:list,
			  tol:float, set_x_func:callable, meas_y_func:callable, min_x:float=None, max_x:float=None, min_y:float=None, max_y:float=None, 
			  t_stabilize:float=1, max_iterations:int=10):
		'''
		The probe_dx will follow these rules:
		 - With no prior knowledge, default_probe_dx will be used
		 - The probe_dx will scale with measured slope and remaining y-error. 
		 - THe probe_dx will never be larger than max_step (just as the new x0 will never change more than max_step).
		 
		'''
		
		self.log = log
		self.log.log_levels.append(plf.LogLevelDefinition(HIGHDEBUG, "HIGHDEBUG", label_color=Fore.LIGHTGREEN_EX))
		
		# Max X distance it is allowed to jump per iteration
		self.max_step = max_step
		
		# Maximum number of iterations allowed per point
		self.max_iterations = max_iterations
		
		# Size of dX to estimate slope
		self.default_probe_dx = default_probe_dx
		
		# If slope too small for estimate, try one of these dXs
		self.backup_slope_tests = backup_slope_tests
		
		self.t_stabilize = t_stabilize
		
		self.target_tolerance = tol
		
		self.current_x = None
		
		# Save callables
		self.set_x_func = set_x_func
		self.meas_y_func = meas_y_func
		
		# Safety, won't excede these xs and ys. (For the ys, it aborts if a setpoint
		# outsuide this range is requested)
		self.min_x = min_x
		self.max_x = max_x
		self.min_y = min_y
		self.max_y = max_y
		
		# Buffer for saving iteration history
		self.history_buffer_max = 500 # Max number of points to save before looping
		self.history_idx = 0 # Next index to (over)write
		self.history_buffer = [] # Buffer 
	
	def _clip_x(self, x_val:float):
		''' Clips an x-value to acceptable limits
		'''
		
		if self.min_x is not None and x_val < self.min_x:
			return self.min_x
		if self.max_x is not None and x_val > self.max_x:
			return self.max_x
		
		return x_val
	
	def _clip_y(self, y_val:float):
		''' Clips an y-value to acceptable limits
		'''
		
		if self.min_y is not None and y_val < self.min_y:
			return self.min_y
		if self.max_y is not None and y_val > self.max_y:
			return self.max_y
		
		return y_val
	
	def _clip_dx(self, dx:float):
		''' Ensures the absolute value of dx is not greater than self.max_step.
		'''
		
		# clip
		if np.abs(dx) > self.max_step:
			
			# Get sign of dx
			sign = -1
			if dx >= 0:
				sign = 1
			
			return self.max_step*sign
			
		else:
			return dx
	
	def set_x_value(self, x:float):
		self.log.debug(f"Controller setting x value to >{x}<.")
		
		self.set_x_func(x)
		self.current_x = x
		
		time.sleep(self.t_stabilize)
	
	def measure_y(self):
		
		y = self.meas_y_func()
		
		#TODO: Measure x?
		
		self.log.debug(f"Controller measured y-value={y} for x-value={self.current_x}")
		
		return y
	
	def _add_to_history(self, point):
		''' Saves a point to the history buffer, respecing buffer size limitations.
		'''
		
		if len(self.history_buffer) < self.history_buffer_max:
			
			# Append to buffer
			self.history_buffer.append(point)
		else:
			# Overwrite next element of buffer
			self.history_buffer[self.history_idx] = point
			self.history_idx += 1
			
			# Reset pointer
			if self.history_idx >= self.history_buffer_max:
				self.history_idx = 0
	
	def _get_last_history(self):
		
		if len(self.history_buffer) < self.history_buffer_max:
			return self.history_buffer[-1]
		else:
			
			# Get last history idx
			last_history_idx = self.history_idx
			if last_history_idx == 0:
				last_history_idx = self.history_buffer_max-1
			
			# Overwrite next element of buffer
			return  self.history_buffer[last_history_idx]
	
	# def _run_iteration(self, set_point:float, probe_dx:float, tol:float, x0:float):
	# 	'''
	# 	'''
	# 	
	# 	self.log.add_log(HIGHDEBUG, f"Beginning iteration with conditions: set_point:>:a{set_point}<, x0:>{x0}<,  probe_dx:>:q{probe_dx}<, tol:>:q{tol}<.")
	# 	
	# 	# Measure y value for given x0, see if error is good.
	# 	x0 = self._clip_x(x0)
	# 	self.set_x_value(x0)
	# 	y0 = self.measure_y()
	# 	
	# 	if is_within_tol(y0, set_point, tol):
	# 		#TODO: Handle match
	# 		pass
	# 	
	# 	#--------- Measure slope ---------
	# 	
	# 	# Get x-limits within bounds
	# 	x_low0 = x0 - probe_dx/2
	# 	x_low_intermed = self._clip_x(x_low0)
	# 	x_high0 = x0 + probe_dx/2 + (x_low_intermed-x_low0)
	# 	x_high = self._clip_x(x_high0)
	# 	x_low = x_low_intermed - (x_high0 - x_high)
	# 	
	# 	# Get ys
	# 	self.set_x_value(x_low)
	# 	y_low = self.measure_y()
	# 	self.set_x_value(x_high)
	# 	y_high = self.measure_y()
	# 	
	# 	# Estimate slope
	# 	slope = (y_high - y_low) / (x_high - x_low)
	# 	
	# 	# Check for slope errors
	# 	if slope == 0 or np.isfinite(slope):
	# 		self.log.warning(f"Invalid slope!")
	# 	
	# 	# Linear approximation for how to get to target
	# 	dx_update = (set_point - y0) / slope
	# 	dx_update = self._clip_dx(dx_update)
	# 	x_new = x0 + dx_update
	# 	
	# 	# For now, hard code probe and tol as fixed
	# 	probe_dx_new = probe_dx
	# 	tol_new = tol
	# 	
	# 	# Record result
	# 	mnip = MonotonicNonlinearIterationPoint(set_point, slope, x0, tol, y0, x_low, x_high, y_low, y_high, x_new, probe_dx_new, tol_new)
	# 	
	# 	# Record point
	# 	self.log.add_log(HIGHDEBUG, f"Finished iteration for set_point:>:a{set_point}<, x0:>{x0}<. Measured slope:>{slope}<, y0:{y0}, dx-update:{dx_update}, new-x0:>{x_new}<, new-probe-dx:{probe_dx_new}, new-tol:{tol_new}.")
	# 	
	# 	self._add_to_history(mnip)
		
		
		
	
	def set_setpoint(self, set_point:float, init_x:float=None):
		
		# Initialize prove_dx with default
		probe_dx = self.default_probe_dx
		
		# Relationship between dx_update and dx_probe. Bigger -> bigger dx_probe.
		probe_dx_scaling = 1
		
		tol = self.target_tolerance
		
		# Select starting point
		if init_x is not None:
			x0 = init_x
		else:
			if self.current_x is not None:
				x0 = self.current_x
			else:
				x0 = 0
		
		for iter_num in range(self.max_iterations):
			
			# # Run an iteration
			# self._run_iteration(set_point, dx=self.default_probe_dx, x0=init_x)
			
			self.log.add_log(HIGHDEBUG, f"Beginning iteration {iter_num+1}/{self.max_iterations} with conditions: set_point:>:q{set_point}<, x0:>:a{rde(x0)}<,  probe_dx:>:q{rde(probe_dx)}<, tol:>:q{rde(tol)}<.")
		
			# Measure y value for given x0, see if error is good.
			x0 = self._clip_x(x0)
			self.set_x_value(x0)
			y0 = self.measure_y()
			
			# Check for conditions complete
			if is_within_tol(y0, set_point, tol):
				self.log.info(f"Set point >:q{rde(set_point)}< reached. x=>:a{rde(x0)}< -\\> y=>:a{rde(y0)}< (tol={rde(tol)}, error={rde(np.abs(y0-set_point))}).")
				
				# Record result
				mnip = MonotonicNonlinearIterationPoint(set_point, None, None, tol, y0, None, None, None, None, None, None, None)
				self._add_to_history(mnip)
				
				break
			
			#--------- Measure slope ---------
			
			# Get x-limits within bounds
			x_low0 = x0 - probe_dx/2
			x_low_intermed = self._clip_x(x_low0)
			x_high0 = x0 + probe_dx/2 + (x_low_intermed-x_low0)
			x_high = self._clip_x(x_high0)
			x_low = x_low_intermed - (x_high0 - x_high)
			
			# Get ys
			self.set_x_value(x_low)
			y_low = self.measure_y()
			self.set_x_value(x_high)
			y_high = self.measure_y()
			
			# Estimate slope
			slope = (y_high - y_low) / (x_high - x_low)
			
			# Check for slope errors
			if slope == 0 or not np.isfinite(slope):
				self.log.warning(f"Invalid slope! Randomizing measurement point. (slope={slope})")
				
				# Randomize measurement point
				if random.random() > 0.5:
					x0 = x0 + self.max_step*staral.randrangef(0.5, 1, 0.05)
				else:
					x0 = x0 - self.max_step*staral.randrangef(0.5, 1, 0.05)
				x0 = self._clip_x(x0)
				
				#TODO: Add to history
				
				continue
			
			#--------- Measure slope ---------
			
			# Linear approximation for how to get to target
			dx_update = (set_point - y0) / slope
			x_new_pt = x0 + dx_update
			dx_update = self._clip_dx(dx_update)
			x_new = x0 + dx_update
			x_new = self._clip_x(x_new)
			
			# Update probe dx
			probe_dx_new = dx_update*probe_dx_scaling
			
			# For now, hard code tol as fixed
			tol_new = tol
			
			# Record result
			mnip = MonotonicNonlinearIterationPoint(set_point, slope, x0, tol, y0, x_low, x_high, y_low, y_high, x_new, probe_dx_new, tol_new)
			
			# Record point
			self.log.add_log(HIGHDEBUG, f"Finished iteration for set_point:>:q{rde(set_point)}<, x0:>:a{rde(x0)}<. Measured slope:>:a{rde(slope)}<, y0:>:a{rde(y0)}<, dx-update:>:q{rde(dx_update)}<, new-x0:>:q{rde(x_new)}<(wanted:>:q{rde(x_new_pt)}<), new-probe-dx:>:a{rde(probe_dx_new)}<, new-tol:>:q{rde(tol_new)}<.")
			self._add_to_history(mnip)
			
			# Update variables for next iteration
			x0 = x_new
			probe_dx = probe_dx_new
			tol = tol_new