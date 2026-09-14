"""RIGOL DP832 / DP832A triple-output power supply.

Commands, reply formats and ranges are from the RIGOL DP800 Series Programming Guide: Table 2-1 for
the settable voltage/current ranges, Table 2-2 for the OVP/OCP ranges.

Values outside the instrument's range are refused here, before anything is sent. The setters read
back afterwards as usual, so the tracked state always holds what the instrument reports.
"""

from constellation.instrument_control.power_supply.power_supply_ctg import *

def _scpi_number(value) -> str:
	''' A number in plain decimal notation, never scientific. Python writes small floats as
	"5e-05", and the DP800 guide documents no exponent form for its real-valued parameters. Six
	decimals is well below the instrument's 0.1 mV / 0.1 mA resolution. '''

	text = f"{float(value):.6f}".rstrip("0").rstrip(".")
	return "0" if text in ("", "-0") else text

def _yes_no(reply:str) -> bool:
	''' The protection-trip queries answer YES or NO - which str_to_bool would read as False either
	way. '''

	return reply.strip().upper() == "YES"

class RigolDP832(PowerSupply):

	# Maximum voltage (V) and current (A) the instrument accepts per channel - Table 2-1. The ratings
	# are 30 V/3 A, 30 V/3 A and 5 V/3 A; the settable ranges run slightly past them.
	VOLTAGE_RANGE = {1: (0.0, 32.0), 2: (0.0, 32.0), 3: (0.0, 5.3)}
	CURRENT_RANGE = {1: (0.0, 3.2), 2: (0.0, 3.2), 3: (0.0, 3.2)}

	# OVP (V) and OCP (A) ranges per channel - Table 2-2. The lower bounds are the DP832A's; a DP832
	# without the high-resolution option only goes down to 10 mV and refuses anything lower itself.
	OVP_RANGE = {1: (0.001, 33.0), 2: (0.001, 33.0), 3: (0.001, 5.5)}
	OCP_RANGE = {1: (0.001, 3.3), 2: (0.001, 3.3), 3: (0.001, 3.3)}

	def __init__(self, address:str, log:plf.LogPile, relay:CommandRelay=None, **kwargs):
		super().__init__(address, log, relay=relay, expected_idn='RIGOL TECHNOLOGIES,DP832', max_channels=3, first_channel=1, **kwargs)

	def _valid_channel(self, channel:int) -> bool:
		if channel in self.VOLTAGE_RANGE:
			return True
		self.error(f"Invalid channel >{channel}<. The DP832 has channels 1 to 3.")
		return False

	def _in_range(self, label:str, channel:int, value:float, ranges:dict, unit:str) -> bool:
		''' True if `value` may be sent to `channel`. Logs and returns False otherwise. '''

		if not self._valid_channel(channel):
			return False

		lo, hi = ranges[channel]
		if lo <= float(value) <= hi:
			return True

		self.error(f"Did not send {label} >{value} {unit}< to channel {channel}: the DP832 accepts {lo:g} to {hi:g} {unit} there.")
		return False

	@superreturn
	def set_voltage(self, channel:int, voltage:float):
		if self._in_range("voltage", channel, voltage, self.VOLTAGE_RANGE, "V"):
			self.write(f":SOUR{channel}:VOLT {_scpi_number(voltage)}")

	@superreturn
	def get_voltage(self, channel:int):
		if self._valid_channel(channel):
			return float(self.query(f":SOUR{channel}:VOLT?").strip())

	@superreturn
	def set_current(self, channel:int, current:float):
		if self._in_range("current", channel, current, self.CURRENT_RANGE, "A"):
			self.write(f":SOUR{channel}:CURR {_scpi_number(current)}")

	@superreturn
	def get_current(self, channel:int):
		if self._valid_channel(channel):
			return float(self.query(f":SOUR{channel}:CURR?").strip())

	@superreturn
	def set_output_enable(self, channel:int, enable:bool):
		if self._valid_channel(channel):
			self.write(f":OUTP CH{channel},{bool_to_ONOFF(enable)}")

	@superreturn
	def get_output_enable(self, channel:int):
		if self._valid_channel(channel):
			return str_to_bool(self.query(f":OUTP? CH{channel}"))

	@superreturn
	def get_measured_voltage(self, channel:int):
		if self._valid_channel(channel):
			return float(self.query(f":MEAS:VOLT? CH{channel}").strip())

	@superreturn
	def get_measured_current(self, channel:int):
		if self._valid_channel(channel):
			return float(self.query(f":MEAS:CURR? CH{channel}").strip())

	@superreturn
	def get_measured_power(self, channel:int):
		if self._valid_channel(channel):
			return float(self.query(f":MEAS:POWE? CH{channel}").strip())

	@superreturn
	def set_ovp_level(self, channel:int, voltage:float):
		if self._in_range("OVP level", channel, voltage, self.OVP_RANGE, "V"):
			self.write(f":OUTP:OVP:VAL CH{channel},{_scpi_number(voltage)}")

	@superreturn
	def get_ovp_level(self, channel:int):
		if self._valid_channel(channel):
			return float(self.query(f":OUTP:OVP:VAL? CH{channel}").strip())

	@superreturn
	def set_ovp_enable(self, channel:int, enable:bool):
		if self._valid_channel(channel):
			self.write(f":OUTP:OVP CH{channel},{bool_to_ONOFF(enable)}")

	@superreturn
	def get_ovp_enable(self, channel:int):
		if self._valid_channel(channel):
			return str_to_bool(self.query(f":OUTP:OVP? CH{channel}"))

	@superreturn
	def get_ovp_tripped(self, channel:int):
		if self._valid_channel(channel):
			return _yes_no(self.query(f":OUTP:OVP:QUES? CH{channel}"))

	@superreturn
	def clear_ovp_trip(self, channel:int):
		if self._valid_channel(channel):
			self.write(f":OUTP:OVP:CLEAR CH{channel}")

	@superreturn
	def set_ocp_level(self, channel:int, current:float):
		if self._in_range("OCP level", channel, current, self.OCP_RANGE, "A"):
			self.write(f":OUTP:OCP:VAL CH{channel},{_scpi_number(current)}")

	@superreturn
	def get_ocp_level(self, channel:int):
		if self._valid_channel(channel):
			return float(self.query(f":OUTP:OCP:VAL? CH{channel}").strip())

	@superreturn
	def set_ocp_enable(self, channel:int, enable:bool):
		if self._valid_channel(channel):
			self.write(f":OUTP:OCP CH{channel},{bool_to_ONOFF(enable)}")

	@superreturn
	def get_ocp_enable(self, channel:int):
		if self._valid_channel(channel):
			return str_to_bool(self.query(f":OUTP:OCP? CH{channel}"))

	@superreturn
	def get_ocp_tripped(self, channel:int):
		if self._valid_channel(channel):
			return _yes_no(self.query(f":OUTP:OCP:QUES? CH{channel}"))

	@superreturn
	def clear_ocp_trip(self, channel:int):
		if self._valid_channel(channel):
			self.write(f":OUTP:OCP:CLEAR CH{channel}")
