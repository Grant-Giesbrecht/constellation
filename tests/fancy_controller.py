from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable, Optional, List
import time
import math


# ----------------------------
# Data containers
# ----------------------------

@dataclass
class Measurement:
	"""One measurement sample (x, y)."""
	x: float
	y: float


@dataclass
class IterationRecord:
	"""One controller iteration (one 'move' and the resulting measurement)."""
	iter_index: int
	x_before: float
	y_before: float
	slope_dy_dx: Optional[float]
	target_y: float
	err_y: float
	dx_cmd: float
	x_cmd: float
	x_applied: float
	x_meas: float
	y_meas: float


@dataclass
class SlopeProbeRecord:
	"""Details of a slope (dy/dx) estimation probe."""
	x0: float
	dx_probe: float
	method: str  # "symmetric" or "one_sided"
	x_lo: float
	x_hi: float
	meas_lo: Optional[Measurement]
	meas_hi: Optional[Measurement]
	meas_0: Optional[Measurement]
	slope_dy_dx: Optional[float]


@dataclass
class SetpointSession:
	"""Full history for one requested setpoint (target_y)."""
	session_id: int
	started_epoch_s: float
	target_y: float
	initial_x: Optional[float]
	config_snapshot: dict
	slope_probe: Optional[SlopeProbeRecord] = None
	iterations: List[IterationRecord] = field(default_factory=list)
	final_measurement: Optional[Measurement] = None
	success: bool = False
	fail_reason: Optional[str] = None


# ----------------------------
# Controller
# ----------------------------

class LocallyLinearSetpointController:
	"""
	Application-agnostic locally-linear setpoint controller.

	You provide:
	  - set_x(x: float) -> None
	  - get_measurement() -> Measurement  (must include measured x + y)

	Goal:
	  Given a target y, choose x such that y(x) ~= target.

	Approach:
	  - Estimate local slope dy/dx with a small probe around current x.
	  - Newton update with guardrails: x <- x + (target_y - y_meas)/slope
	  - Repeat until |error| <= tolerance_y or max_iters reached.

	History:
	  - controller.sessions: list of SetpointSession
	  - Each session records: target, slope probe details, every iteration, final result.
	"""

	def __init__(
		self,
		set_x: Callable[[float], None],
		get_measurement_y: Callable[[], float],
		*,
		# Core convergence parameters
		tolerance_y: float = 1e-6,
		max_iters: int = 12,
		get_measurement_x: Callable[[], float] = None,
		# Timing / averaging
		settle_s: float = 0.15,
		avg_n: int = 5,
		avg_delay_s: float = 0.01,
		# X constraints
		x_min: Optional[float] = None,
		x_max: Optional[float] = None,
		max_step_x: float = 0.25,
		# Slope probing
		dx_probe: float = 0.02,
		min_abs_slope: float = 1e-9,  # |dy/dx| minimum usable
		# Robustness / stall handling
		remeasure_slope_each_setpoint: bool = True,
		remeasure_on_stall: bool = True,
		stall_err_improve_frac: float = 0.10,
		stall_patience: int = 2,
		# Optional: safety clamp if y overshoots wildly
		max_abs_y: Optional[float] = None,
		# History
		keep_history: bool = True,
		max_sessions: Optional[int] = None,  # e.g. 200 to prevent unbounded growth
		# Optional: log hook
		log: Optional[Callable[[str], None]] = None,
	):
		self.set_x = set_x
		self.get_measurement_y = get_measurement_y
		self.get_measurement_x = get_measurement_x
		
		self._set_point = None

		self.tolerance_y = tolerance_y
		self.max_iters = max_iters

		self.settle_s = settle_s
		self.avg_n = avg_n
		self.avg_delay_s = avg_delay_s

		self.x_min = x_min
		self.x_max = x_max
		self.max_step_x = max_step_x

		self.dx_probe = dx_probe
		self.min_abs_slope = min_abs_slope

		self.remeasure_slope_each_setpoint = remeasure_slope_each_setpoint
		self.remeasure_on_stall = remeasure_on_stall
		self.stall_err_improve_frac = stall_err_improve_frac
		self.stall_patience = stall_patience

		self.max_abs_y = max_abs_y

		self.keep_history = keep_history
		self.max_sessions = max_sessions

		self.log = log or (lambda _: None)

		# Cached slope (dy/dx) from last operation
		self._slope_cache: Optional[float] = None

		# History store
		self.sessions: List[SetpointSession] = []
		self._next_session_id = 1

	# -----------------------
	# Public API
	# -----------------------

	def set_target_y(self, target_y: float, *, initial_x: Optional[float] = None) -> Measurement:
		"""
		Drive the system to target y by adjusting x.

		Returns final averaged measurement.
		Raises RuntimeError on failure to converge / invalid slope / safety violation.
		"""
		session = self._start_session(target_y=target_y, initial_x=initial_x)
		
		self._set_point = target_y
		
		try:
			if initial_x is not None:
				self._apply_x(initial_x)
			
			m = self._measure_avg()
			self._safety_check(m)
			
			# Re-measure slope across setpoint changes (or if missing)
			if self.remeasure_slope_each_setpoint or (self._slope_cache is None):
				slope, probe = self._estimate_slope_about(m.x)
				self._slope_cache = slope
				session.slope_probe = probe
			else:
				slope = self._slope_cache
			
			if slope is None:
				raise RuntimeError("Failed to estimate slope (dy/dx).")
			
			best_m = m
			best_err = abs(target_y - m.y)
			non_improve_count = 0

			for k in range(self.max_iters):
				err = target_y - m.y
				abs_err = abs(err)
				
				self.log(f"[iter {k}] x={m.x:.6g}, y={m.y:.6g}, err={err:.6g}, slope={slope:.6g}")

				if abs_err <= self.tolerance_y:
					session.final_measurement = m
					session.success = True
					return m

				if abs(slope) < self.min_abs_slope:
					raise RuntimeError(
						f"Slope too small: |dy/dx|={abs(slope):.3g} (min {self.min_abs_slope:.3g})."
					)

				# Newton step with guardrails
				dx_cmd = err / slope
				dx_cmd_clipped = self._clip(dx_cmd, -self.max_step_x, self.max_step_x)

				x_before = m.x
				y_before = m.y

				x_cmd = x_before + dx_cmd_clipped
				x_applied = self._clip_x(x_cmd)

				if x_applied == x_before:
					raise RuntimeError(
						"x update clipped to zero (likely at x_min/x_max or max_step_x too small). "
						f"x={x_before:.6g}, x_cmd={x_cmd:.6g}, x_applied={x_applied:.6g}, "
						f"x_min={self.x_min}, x_max={self.x_max}, max_step_x={self.max_step_x}"
					)

				self._apply_x(x_applied)
				m2 = self._measure_avg()
				self._safety_check(m2)

				# Record iteration (captures command + result)
				session.iterations.append(
					IterationRecord(
						iter_index=k,
						x_before=x_before,
						y_before=y_before,
						slope_dy_dx=slope,
						target_y=target_y,
						err_y=err,
						dx_cmd=dx_cmd,           # unclipped (debuggable)
						x_cmd=x_cmd,             # unclipped (except for max_step clip in dx)
						x_applied=x_applied,     # after x_min/x_max clip
						x_meas=m2.x,
						y_meas=m2.y,
					)
				)

				m = m2
				new_err = abs(target_y - m.y)

				if new_err < best_err:
					best_err = new_err
					best_m = m

				improved = (new_err <= (1.0 - self.stall_err_improve_frac) * abs_err)
				if improved:
					non_improve_count = 0
				else:
					non_improve_count += 1

				if self.remeasure_on_stall and non_improve_count >= self.stall_patience:
					self.log(f"[iter {k}] Stall detected; re-estimating slope about x={m.x:.6g}")
					slope, probe = self._estimate_slope_about(m.x)
					self._slope_cache = slope
					session.slope_probe = probe  # replace with latest probe for this session
					non_improve_count = 0
					if slope is None:
						raise RuntimeError("Failed to re-estimate slope during stall recovery.")

			raise RuntimeError(
				f"Failed to converge in {self.max_iters} iterations. "
				f"Best: x={best_m.x:.6g}, y={best_m.y:.6g}, |err|={best_err:.6g} (tol {self.tolerance_y:.6g})."
			)

		except Exception as e:
			session.final_measurement = session.final_measurement or self._safe_try_measure()
			session.success = False
			session.fail_reason = str(e)
			raise
		finally:
			self._finish_session(session)

	def clear_slope_cache(self) -> None:
		"""Forces slope re-measurement on next set_target_y call."""
		self._slope_cache = None

	def clear_history(self) -> None:
		self.sessions.clear()

	def last_session(self) -> Optional[SetpointSession]:
		return self.sessions[-1] if self.sessions else None

	# -----------------------
	# History handling
	# -----------------------

	def _start_session(self, *, target_y: float, initial_x: Optional[float]) -> SetpointSession:
		session = SetpointSession(
			session_id=self._next_session_id,
			started_epoch_s=time.time(),
			target_y=float(target_y),
			initial_x=None if initial_x is None else float(initial_x),
			config_snapshot=self._config_snapshot(),
		)
		self._next_session_id += 1
		return session

	def _finish_session(self, session: SetpointSession) -> None:
		if not self.keep_history:
			return
		self.sessions.append(session)
		if self.max_sessions is not None and len(self.sessions) > self.max_sessions:
			# Drop oldest
			overflow = len(self.sessions) - self.max_sessions
			del self.sessions[:overflow]

	def _config_snapshot(self) -> dict:
		# Only include the parameters that affect behavior (and are useful for debugging).
		return {
			"tolerance_y": self.tolerance_y,
			"max_iters": self.max_iters,
			"settle_s": self.settle_s,
			"avg_n": self.avg_n,
			"avg_delay_s": self.avg_delay_s,
			"x_min": self.x_min,
			"x_max": self.x_max,
			"max_step_x": self.max_step_x,
			"dx_probe": self.dx_probe,
			"min_abs_slope": self.min_abs_slope,
			"remeasure_slope_each_setpoint": self.remeasure_slope_each_setpoint,
			"remeasure_on_stall": self.remeasure_on_stall,
			"stall_err_improve_frac": self.stall_err_improve_frac,
			"stall_patience": self.stall_patience,
			"max_abs_y": self.max_abs_y,
		}

	def _safe_try_measure(self) -> Optional[Measurement]:
		try:
			return self._measure_avg()
		except Exception:
			return None

	# -----------------------
	# Internals: actuation / measurement
	# -----------------------

	def _apply_x(self, x: float) -> None:
		self.log(f"Setting new X value = {x}", 20)
		x = self._clip_x(x)
		self.set_x(x)
		if self.settle_s > 0:
			time.sleep(self.settle_s)

	def _measure_avg(self) -> Measurement:
		"""
		Average avg_n samples of (x, y). We average x too in case the instrument
		reports actual output (compliance, droop, etc).
		"""
		n = max(1, int(self.avg_n))
		sum_x = 0.0
		sum_y = 0.0

		for idx in range(n):
			if self.get_measurement_x is None:
				m_x = self._set_point
			else:
				m_x = self.get_measurement_x()
			m_y = self.get_measurement_y()
			sum_x += float(m_x)
			sum_y += float(m_y)
			if idx != n - 1 and self.avg_delay_s > 0:
				time.sleep(self.avg_delay_s)

		return Measurement(x=sum_x / n, y=sum_y / n)

	def _safety_check(self, m: Measurement) -> None:
		if self.max_abs_y is not None and abs(m.y) > self.max_abs_y:
			raise RuntimeError(
				f"Safety: |y|={abs(m.y):.3g} exceeds max_abs_y={self.max_abs_y:.3g}."
			)

	# -----------------------
	# Internals: slope estimation
	# -----------------------

	def _estimate_slope_about(self, x0: float) -> tuple[Optional[float], SlopeProbeRecord]:
		"""
		Two-point slope estimate near x0:
			slope ≈ (y(x0+dx) - y(x0-dx)) / ( (x0+dx)-(x0-dx) )

		Falls back to one-sided if rails prevent symmetric probing.
		Returns: (slope, probe_record)
		"""
		
		self.log(f"Estimating slope around point {x0}.")
		
		dx = abs(float(self.dx_probe))
		probe = SlopeProbeRecord(
			x0=float(x0),
			dx_probe=dx,
			method="unknown",
			x_lo=float("nan"),
			x_hi=float("nan"),
			meas_lo=None,
			meas_hi=None,
			meas_0=None,
			slope_dy_dx=None,
		)

		if dx == 0:
			self.log(f"Failed to estimate slope, dx=0.", 40)
			return None, probe

		x_lo = self._clip_x(x0 - dx)
		x_hi = self._clip_x(x0 + dx)
		probe.x_lo = x_lo
		probe.x_hi = x_hi

		if x_hi == x_lo:
			self.log(f"Failed to estimate slope, blocked.", 40)
			probe.method = "blocked"
			return None, probe

		# Prefer symmetric probe if possible
		if (x_hi - x0) > 0 and (x0 - x_lo) > 0:
			probe.method = "symmetric"

			self._apply_x(x_hi)
			m_hi = self._measure_avg()

			self._apply_x(x_lo)
			m_lo = self._measure_avg()
			
			# Return to x0 for continuity
			self._apply_x(x0)
			
			slope = (m_hi.y - m_lo.y) / (m_hi.x - m_lo.x) if (m_hi.x != m_lo.x) else None
			if slope is not None and not math.isfinite(slope):
				self.log(f"Failed to estimate slope, was infinite.", 40)
				slope = None
			
			probe.meas_hi = m_hi
			probe.meas_lo = m_lo
			probe.slope_dy_dx = slope
			self.log(f"Reporting slope value of {slope}. (symmetric, m_hi:{m_hi.x}, {m_hi.y}, m_lo:{m_lo.x},{m_lo.y} )", 20)
			return slope, probe

		# One-sided near rails
		probe.method = "one_sided"

		# Choose side that actually moves
		if x_hi != x0:
			self._apply_x(x0)
			m_0 = self._measure_avg()
			self._apply_x(x_hi)
			m_hi = self._measure_avg()
			self._apply_x(x0)

			slope = (m_hi.y - m_0.y) / (m_hi.x - m_0.x) if (m_hi.x != m_0.x) else None
			if slope is not None and not math.isfinite(slope):
				self.log(f"Failed to estimate slope, was infinite.", 40)
				slope = None

			probe.meas_0 = m_0
			probe.meas_hi = m_hi
			probe.slope_dy_dx = slope
			self.log(f"Reporting slope value of {slope}. (hi!=0)", 20)
			return slope, probe

		if x_lo != x0:
			self._apply_x(x0)
			m_0 = self._measure_avg()
			self._apply_x(x_lo)
			m_lo = self._measure_avg()
			self._apply_x(x0)

			slope = (m_0.y - m_lo.y) / (m_0.x - m_lo.x) if (m_0.x != m_lo.x) else None
			if slope is not None and not math.isfinite(slope):
				self.log(f"Failed to estimate slope, was infinite.", 40)
				slope = None

			probe.meas_0 = m_0
			probe.meas_lo = m_lo
			probe.slope_dy_dx = slope
			self.log(f"Reporting slope value of {slope}. (hi!=0)", 20)
			return slope, probe
		
		self.log(f"Failed to estimate slope, reached end of function.", 40)
		return None, probe

	# -----------------------
	# Utilities
	# -----------------------

	def _clip_x(self, x: float) -> float:
		if self.x_min is not None:
			x = max(self.x_min, x)
		if self.x_max is not None:
			x = min(self.x_max, x)
		return x
	
	@staticmethod
	def _clip(x: float, lo: float, hi: float) -> float:
		return max(lo, min(hi, x))
