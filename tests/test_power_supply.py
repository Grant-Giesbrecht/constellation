""" The power supply category and the Rigol DP832 driver: measured readings, protection, value
formatting and range checks.

Runs without hardware - dummy mode for the category behaviour, and a relay replaying canned SCPI
replies for the real driver bodies.
"""

import inspect

import pytest
import pylogfile.base as plf

from constellation.relay import CommandRelay
from constellation.instrument_control.power_supply.power_supply_ctg import PowerSupply, PowerSupplyChannelState
from constellation.instrument_control.power_supply.drivers.Rigol_DP832_dvr import RigolDP832, _scpi_number

def make_log():
	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL  # keep test output quiet
	return log

class _CannedRelay(CommandRelay):
	""" Replays canned SCPI replies, matched by command prefix; anything unlisted answers "0". """

	def __init__(self, table=None):
		super().__init__()
		self.table = {"*IDN?": "RIGOL TECHNOLOGIES,DP832,DP3A000000000,00.01.14"}
		self.table.update(table or {})
		self.sent = []

	def connect(self): return True
	def close(self): pass
	def write(self, cmd): self.sent.append(cmd); return True
	def read(self): return True, ""

	def query(self, cmd):
		self.sent.append(cmd)
		for prefix, reply in self.table.items():
			if cmd.startswith(prefix):
				return True, reply
		return True, "0"

def make_dummy():
	return RigolDP832("DUMMY", make_log(), dummy=True)

def make_canned(table=None):
	return RigolDP832("canned", make_log(), relay=_CannedRelay(table))

def writes(psu):
	return [c for c in psu.relay.sent if "?" not in c]

# ---------------------------------------------------------------------------
# Category shape
# ---------------------------------------------------------------------------

def test_every_setter_sets_one_parameter():
	for name, fn in inspect.getmembers(PowerSupply, inspect.isfunction):
		if name.startswith("set_"):
			params = [p for p in inspect.signature(fn).parameters if p not in ("self", "channel")]
			assert len(params) == 1, f"PowerSupply.{name} sets {params}"

def test_every_measured_getter_returns_one_number():
	psu = make_dummy()
	psu.set_output_enable(1, True)
	for name in ("get_measured_voltage", "get_measured_current", "get_measured_power"):
		assert isinstance(getattr(psu, name)(1), float), name

def test_readings_are_flagged_as_data_and_settings_are_not():
	assert set(PowerSupplyChannelState().is_data) == {"voltage_meas", "current_meas", "power_meas"}

# ---------------------------------------------------------------------------
# Dummy mode
# ---------------------------------------------------------------------------

def test_dummy_readings_follow_the_setpoints_while_the_output_is_on():
	psu = make_dummy()
	psu.set_voltage(2, 3.3)
	psu.set_current(2, 0.25)
	psu.set_output_enable(2, True)

	voltage = psu.get_measured_voltage(2)
	current = psu.get_measured_current(2)

	assert abs(voltage - 3.3) < 0.2
	assert abs(current - 0.25) < 0.1
	assert abs(psu.get_measured_power(2) - 3.3 * 0.25) < 1.0
	assert psu.get_measured_voltage(2) != voltage          # a reading, not a stored value

def test_dummy_readings_are_near_zero_while_the_output_is_off():
	psu = make_dummy()
	psu.set_voltage(1, 12.0)
	psu.set_output_enable(1, False)

	assert abs(psu.get_measured_voltage(1)) < 0.01
	assert abs(psu.get_measured_current(1)) < 0.01

def test_dummy_readings_are_recorded_in_state():
	psu = make_dummy()
	psu.set_output_enable(3, True)

	voltage = psu.get_measured_voltage(3)
	power = psu.get_measured_power(3)

	assert psu.state.channels[3].voltage_meas == voltage
	assert psu.state.channels[3].power_meas == power

def test_dummy_protection_settings_round_trip():
	psu = make_dummy()
	psu.set_ovp_level(1, 12.5)
	psu.set_ovp_enable(1, True)
	psu.set_ocp_level(1, 1.5)
	psu.set_ocp_enable(1, True)

	assert psu.get_ovp_level(1) == 12.5
	assert psu.get_ovp_enable(1) is True
	assert psu.get_ocp_level(1) == 1.5
	assert psu.get_ocp_enable(1) is True

def test_dummy_starts_untripped_and_clearing_leaves_it_untripped():
	psu = make_dummy()
	assert psu.get_ovp_tripped(1) is False
	assert psu.get_ocp_tripped(1) is False

	psu.state.set(["channels", "ovp_tripped"], True, indices=[1])
	psu.clear_ovp_trip(1)
	assert psu.get_ovp_tripped(1) is False

def test_refresh_and_apply_do_not_raise():
	psu = make_dummy()
	psu.refresh_state()
	psu.refresh_data()
	psu.apply_state()

# ---------------------------------------------------------------------------
# DP832 commands (real driver bodies, canned replies)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call,expected", [
	(lambda p: p.set_voltage(2, 12.0), ":SOUR2:VOLT 12"),
	(lambda p: p.set_current(1, 0.5), ":SOUR1:CURR 0.5"),
	(lambda p: p.set_output_enable(3, True), ":OUTP CH3,ON"),
	(lambda p: p.get_measured_voltage(1), ":MEAS:VOLT? CH1"),
	(lambda p: p.get_measured_current(2), ":MEAS:CURR? CH2"),
	(lambda p: p.get_measured_power(3), ":MEAS:POWE? CH3"),
	(lambda p: p.set_ovp_level(1, 8.8), ":OUTP:OVP:VAL CH1,8.8"),
	(lambda p: p.get_ovp_level(1), ":OUTP:OVP:VAL? CH1"),
	(lambda p: p.set_ovp_enable(2, True), ":OUTP:OVP CH2,ON"),
	(lambda p: p.get_ovp_enable(2), ":OUTP:OVP? CH2"),
	(lambda p: p.get_ovp_tripped(3), ":OUTP:OVP:QUES? CH3"),
	(lambda p: p.clear_ovp_trip(2), ":OUTP:OVP:CLEAR CH2"),
	(lambda p: p.set_ocp_level(3, 1.5), ":OUTP:OCP:VAL CH3,1.5"),
	(lambda p: p.get_ocp_level(3), ":OUTP:OCP:VAL? CH3"),
	(lambda p: p.set_ocp_enable(1, False), ":OUTP:OCP CH1,OFF"),
	(lambda p: p.get_ocp_enable(1), ":OUTP:OCP? CH1"),
	(lambda p: p.get_ocp_tripped(2), ":OUTP:OCP:QUES? CH2"),
	(lambda p: p.clear_ocp_trip(3), ":OUTP:OCP:CLEAR CH3"),
])
def test_dp832_sends_the_documented_command(call, expected):
	psu = make_canned()
	psu.relay.sent.clear()
	call(psu)
	assert expected in psu.relay.sent

def test_measured_readings_reach_state():
	psu = make_canned({":MEAS:VOLT? CH1": "4.9980", ":MEAS:CURR? CH1": "0.1020", ":MEAS:POWE? CH1": "0.510"})

	assert psu.get_measured_voltage(1) == 4.998
	assert psu.get_measured_current(1) == 0.102
	assert psu.get_measured_power(1) == 0.51
	assert psu.state.channels[1].current_meas == 0.102

@pytest.mark.parametrize("reply,expected", [("YES", True), ("NO", False), ("YES\n", True)])
def test_trip_queries_parse_yes_and_no(reply, expected):
	""" str_to_bool reads "YES" as False, so a tripped protection would have reported untripped. """
	psu = make_canned({":OUTP:OVP:QUES?": reply, ":OUTP:OCP:QUES?": reply})
	assert psu.get_ovp_tripped(1) is expected
	assert psu.get_ocp_tripped(1) is expected

def test_small_values_are_sent_without_scientific_notation():
	psu = make_canned()
	psu.relay.sent.clear()
	psu.set_current(1, 0.00005)

	assert ":SOUR1:CURR 0.00005" in psu.relay.sent
	assert not any("e-" in c.lower() for c in psu.relay.sent)

@pytest.mark.parametrize("value,expected", [
	(12.0, "12"), (3.3, "3.3"), (0.00005, "0.00005"), (0, "0"), (-0.0, "0"), (1e-7, "0"), (-1.25, "-1.25"),
])
def test_scpi_number_formatting(value, expected):
	assert _scpi_number(value) == expected

@pytest.mark.parametrize("call,prefix", [
	(lambda p: p.set_voltage(3, 6.0), ":SOUR3:VOLT"),       # channel 3 tops out at 5.3 V
	(lambda p: p.set_voltage(1, 33.0), ":SOUR1:VOLT"),
	(lambda p: p.set_current(2, 3.5), ":SOUR2:CURR"),
	(lambda p: p.set_voltage(1, -1.0), ":SOUR1:VOLT"),
	(lambda p: p.set_ovp_level(3, 6.0), ":OUTP:OVP:VAL"),
	(lambda p: p.set_ocp_level(1, 4.0), ":OUTP:OCP:VAL"),
	(lambda p: p.set_voltage(4, 1.0), ":SOUR4"),
	(lambda p: p.set_output_enable(0, True), ":OUTP CH0"),
])
def test_out_of_range_values_are_not_sent(call, prefix):
	psu = make_canned()
	psu.relay.sent.clear()
	call(psu)
	assert not any(c.startswith(prefix) and "?" not in c for c in psu.relay.sent)

def test_values_between_the_rating_and_the_settable_maximum_are_sent():
	""" The DP832 is rated 30 V on channel 1 but accepts up to 32 V. """
	psu = make_canned()
	psu.relay.sent.clear()
	psu.set_voltage(1, 31.0)
	assert ":SOUR1:VOLT 31" in psu.relay.sent

def test_apply_state_sets_protection_before_switching_the_output_on():
	psu = make_canned()
	for field, value in (("voltage_set", 5.0), ("current_set", 1.0), ("ovp_level", 6.0), ("ovp_enable", True),
			("ocp_level", 1.2), ("ocp_enable", True), ("enable", True)):
		psu.state.set(["channels", field], value, indices=[1])

	psu.relay.sent.clear()
	psu.apply_state()
	sent = writes(psu)

	assert ":OUTP CH1,ON" in sent
	for protection in (":OUTP:OVP:VAL CH1,6", ":OUTP:OVP CH1,ON", ":OUTP:OCP:VAL CH1,1.2", ":OUTP:OCP CH1,ON"):
		assert sent.index(protection) < sent.index(":OUTP CH1,ON")

def test_refresh_state_reads_protection_and_readings():
	psu = make_canned()
	psu.relay.sent.clear()
	psu.refresh_state()

	for command in (":OUTP:OVP:QUES? CH3", ":OUTP:OCP:VAL? CH2", ":MEAS:POWE? CH1", ":MEAS:CURR? CH3"):
		assert command in psu.relay.sent
