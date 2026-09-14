""" Hardware verification for the power-supply category.

Run against an instrument on the bench:

	pytest tests/hardware --driver=RigolDP832 --address=TCPIP0::192.168.1.238::INSTR
	pytest tests/hardware --driver=RigolDP832 --address=TCPIP0::192.168.1.238::INSTR --confirm

The first form is round-trip mode: set a parameter, read it back, check they agree - a `roundtrip`
record. The second pauses after each check for a human to look at the front panel, because a round
trip can be self-consistently wrong: a driver that writes the OCP level where it means the current
limit reads its own mistake back perfectly. Passing moderated mode earns a `confirmed` record.

WHAT THIS DRIVES INTO THE WORLD. A power supply's output is live. **Disconnect any load before
running.** Every check starts from BASELINE_VOLTS with a BASELINE_AMPS current limit, and the output
is only switched on where a check needs it: the output-enable check, and the measured-value check,
which needs a voltage at the terminals to measure. Protection levels stay well above the output, so
nothing is tripped on purpose - which means a trip query can only be checked in its "not tripped"
state, and clearing a trip only by an operator. The `instrument` fixture restores the entry state
at the end of the run.

Results are written to the driver's verification.yaml at the end of the run. See
docs/hardware_verification.md.
"""

import time

import pytest

from constellation.instrument_control.power_supply.power_supply_ctg import PowerSupply
from constellation.verification import VerificationStatus

from hardware_support import (Check, already_confirmed, close_enough, requires_category, run_check,
	skip_if_unavailable)

pytestmark = pytest.mark.hardware

@pytest.fixture(autouse=True)
def _category(instrument):
	''' A run has one instrument on the bench but collects every category module, so a module that
	is not about that instrument steps aside rather than failing. '''

	requires_category(instrument, PowerSupply)

BASELINE_VOLTS = 1.0
BASELINE_AMPS = 0.1

def _baseline(psu, ch) -> None:
	''' Output off, 1 V, 100 mA limit.

	Uses methods that are themselves under test, which is unavoidable: a setpoint can't be checked
	without first putting the channel somewhere known. Steps the driver declares it can't do are
	skipped.
	'''

	for name, args in (
		("set_output_enable", (ch, False)),
		("set_voltage", (ch, BASELINE_VOLTS)),
		("set_current", (ch, BASELINE_AMPS)),
	):
		if psu.feature_is_available(name):
			getattr(psu, name)(*args)

def _same_bool(expected, actual) -> bool:
	return actual is not None and bool(expected) == bool(actual)

# Every set/get pair the PowerSupply category declares. Values stay within every DP832 channel's
# range, channel 3's 5 V included, so --channel may pick any of them.
CHECKS = [

	Check(
		methods=("set_voltage", "get_voltage"),
		values=(BASELINE_VOLTS, 2.5),
		apply=lambda psu, ch, value: psu.set_voltage(ch, value),
		read=lambda psu, ch: psu.get_voltage(ch),
		matches=lambda expected, actual: close_enough(expected, actual, abs_tol=0.005),
		setup=_baseline,
		prompt=lambda psu, ch, value: (
			f"Channel {ch}'s VOLTAGE SETPOINT should read {value:g} V. The output is off, so look at "
			f"the set value, not a measured one. Check the CHANNEL too: a driver that writes the wrong "
			f"channel round-trips perfectly if its getter reads the same wrong channel."),
	),

	Check(
		methods=("set_current", "get_current"),
		values=(BASELINE_AMPS, 0.25),
		apply=lambda psu, ch, value: psu.set_current(ch, value),
		read=lambda psu, ch: psu.get_current(ch),
		matches=lambda expected, actual: close_enough(expected, actual, abs_tol=0.002),
		setup=_baseline,
		prompt=lambda psu, ch, value: (
			f"Channel {ch}'s CURRENT LIMIT should read {value:g} A. It is the setpoint beside the "
			f"voltage, not the OCP value in the protection settings - both are amps, and a driver "
			f"that writes one where it means the other reads it back without complaint."),
	),

	Check(
		methods=("set_ovp_level", "get_ovp_level"),
		values=(5.0, 4.0),
		apply=lambda psu, ch, value: psu.set_ovp_level(ch, value),
		read=lambda psu, ch: psu.get_ovp_level(ch),
		matches=lambda expected, actual: close_enough(expected, actual, abs_tol=0.01),
		setup=_baseline,
		prompt=lambda psu, ch, value: (
			f"Channel {ch}'s OVP VALUE in its protection settings should read {value:g} V, and the "
			f"voltage setpoint should still be {BASELINE_VOLTS:g} V. If the setpoint changed instead, "
			f"the driver is writing the voltage, not the protection level."),
	),

	Check(
		methods=("set_ovp_enable", "get_ovp_enable"),
		# Ends on True so the operator is asked about the state with a visible indicator. OVP on at
		# a level well above a 1 V setpoint, with the output off, cannot trip.
		values=(False, True),
		apply=lambda psu, ch, value: psu.set_ovp_enable(ch, value),
		read=lambda psu, ch: psu.get_ovp_enable(ch),
		matches=_same_bool,
		setup=_baseline,
		prompt=lambda psu, ch, value: (
			f"Over-voltage protection should now be ON for channel {ch} (its OVP indicator or "
			f"protection menu), and OCP should be unchanged. Check the other channels too."),
	),

	Check(
		methods=("set_ocp_level", "get_ocp_level"),
		values=(1.0, 0.5),
		apply=lambda psu, ch, value: psu.set_ocp_level(ch, value),
		read=lambda psu, ch: psu.get_ocp_level(ch),
		matches=lambda expected, actual: close_enough(expected, actual, abs_tol=0.002),
		setup=_baseline,
		prompt=lambda psu, ch, value: (
			f"Channel {ch}'s OCP VALUE in its protection settings should read {value:g} A, and the "
			f"current limit should still be {BASELINE_AMPS:g} A. If the current limit changed "
			f"instead, the driver is writing the setpoint, not the protection level."),
	),

	Check(
		methods=("set_ocp_enable", "get_ocp_enable"),
		values=(False, True),
		apply=lambda psu, ch, value: psu.set_ocp_enable(ch, value),
		read=lambda psu, ch: psu.get_ocp_enable(ch),
		matches=_same_bool,
		setup=_baseline,
		prompt=lambda psu, ch, value: (
			f"Over-current protection should now be ON for channel {ch}, and OVP should be unchanged. "
			f"Check the other channels too."),
	),

	Check(
		methods=("set_output_enable", "get_output_enable"),
		# Ends on True, at the 1 V / 100 mA baseline, so what appears at the terminals is benign
		# and known. The instrument fixture restores the entry state when the run finishes.
		values=(False, True),
		apply=lambda psu, ch, value: psu.set_output_enable(ch, value),
		read=lambda psu, ch: psu.get_output_enable(ch),
		matches=_same_bool,
		setup=_baseline,
		prompt=lambda psu, ch, value: (
			f"Channel {ch}'s OUTPUT should now be ON - its output key lit, about {BASELINE_VOLTS:g} V "
			f"at its terminals. Every other channel's output should be untouched."),
	),
]

@pytest.mark.parametrize("check", CHECKS, ids=[c.id for c in CHECKS])
def test_roundtrip(check, instrument, channel, recorder, confirm, moderated, request):
	""" Set a parameter, read it back, and (in moderated mode) have a human confirm the instrument
	physically did it. """

	run_check(check, instrument, channel, recorder, confirm, moderated, request.config.getoption("--recheck"))

def test_measured_output(instrument, channel, recorder, confirm, moderated, request):
	""" The readings have no setter, so there is no round trip in the usual sense. With no load, the
	measured voltage has to agree with the setpoint - which a driver reading the setpoint instead of
	the measurement also passes, so only a human comparing the front panel's readings earns more.
	Current and power are near zero with no load and have nothing to agree with, so they are
	recorded only in moderated mode. """

	methods = ["get_measured_voltage", "get_measured_current", "get_measured_power"]
	skip_if_unavailable(type(instrument), methods + ["set_output_enable"])

	_baseline(instrument, channel)
	instrument.set_output_enable(channel, True)
	time.sleep(0.5)   # let the output settle before measuring it

	try:
		voltage = instrument.get_measured_voltage(channel)
		current = instrument.get_measured_current(channel)
		power = instrument.get_measured_power(channel)
	except Exception as e:
		recorder.record(methods, VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
		raise
	finally:
		instrument.set_output_enable(channel, False)

	for name, value in zip(methods, (voltage, current, power)):
		if value is None:
			recorder.record(name, VerificationStatus.FAILED, note="returned None")
			pytest.fail(f"{name} returned None")

	# A dummy supply's readings are noise around the setpoint; the agreement check means nothing there.
	if not request.config.getoption("--dummy") and not close_enough(BASELINE_VOLTS, voltage, abs_tol=0.05):
		recorder.record("get_measured_voltage", VerificationStatus.FAILED, note=f"measured {voltage!r} V with a {BASELINE_VOLTS} V setpoint and no load")
		pytest.fail(f"get_measured_voltage: measured {voltage} V, setpoint {BASELINE_VOLTS} V")

	answer = confirm(
		f"While channel {ch_label(channel)}'s output was on at {BASELINE_VOLTS:g} V with no load, "
		f"Constellation read {voltage:.4f} V, {current:.4f} A, {power:.4f} W. Do those match the "
		f"front panel's measured readings for that channel (not its setpoints)?")

	if answer is False:
		recorder.record(methods, VerificationStatus.FAILED, note="operator reported the readings did not match the front panel")
		pytest.fail("operator reported the readings did not match the front panel")

	if answer is True:
		recorder.record(methods, VerificationStatus.CONFIRMED)
	else:
		recorder.record("get_measured_voltage", VerificationStatus.ROUNDTRIP)

def ch_label(channel) -> str:
	return str(channel)

@pytest.mark.parametrize("key", ["ovp", "ocp"])
def test_protection_not_tripped(key, instrument, channel, recorder, confirm, moderated):
	""" Nothing is tripped on purpose, so this only checks the "not tripped" reply parses. A driver
	that always answers False passes that too - which is why moderated mode asks. """

	name = f"get_{key}_tripped"
	skip_if_unavailable(type(instrument), [name])

	_baseline(instrument, channel)

	try:
		tripped = getattr(instrument, name)(channel)
	except Exception as e:
		recorder.record(name, VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
		raise

	if not isinstance(tripped, bool):
		recorder.record(name, VerificationStatus.FAILED, note=f"returned {tripped!r}, not a bool")
		pytest.fail(f"{name} returned {tripped!r}")

	answer = confirm(
		f"Constellation reports channel {channel}'s {key.upper()} as {'TRIPPED' if tripped else 'not tripped'}. "
		f"Does the front panel agree? (If a protection label is showing from before this run, the "
		f"answer should have been 'tripped'.)")

	if answer is False:
		recorder.record(name, VerificationStatus.FAILED, note="operator reported the trip state was wrong")
		pytest.fail(f"{name}: operator reported the trip state was wrong")

	recorder.record(name, VerificationStatus.CONFIRMED if answer else VerificationStatus.ROUNDTRIP)

@pytest.mark.parametrize("key", ["ovp", "ocp"])
def test_clear_trip(key, instrument, channel, recorder, confirm, moderated, request):
	""" An action with no read-back of its own worth trusting, so only a human can verify it. """

	name = f"clear_{key}_trip"
	skip_if_unavailable(type(instrument), [name])

	if not moderated:
		pytest.skip(f"{name}() can only be verified by an operator - run with --confirm")

	if not request.config.getoption("--recheck") and already_confirmed(type(instrument), [name], model=recorder.model, idn=recorder.idn):
		pytest.skip(f"already confirmed on {recorder.model or 'this model'} - pass --recheck to re-run")

	try:
		getattr(instrument, name)(channel)
	except Exception as e:
		recorder.record(name, VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
		raise

	answer = confirm(
		f"Channel {channel} should show no {key.upper()} tripped label, and nothing else about the "
		f"channel should have changed - in particular its output state. (Nothing was tripped, so this "
		f"only confirms the command is accepted and harmless; clearing a real trip needs a load.)")

	if answer is False:
		recorder.record(name, VerificationStatus.FAILED, note="operator reported the clear did the wrong thing")
		pytest.fail(f"{name}: operator reported the instrument did the wrong thing")

	if answer is True:
		recorder.record(name, VerificationStatus.CONFIRMED)
	else:
		pytest.skip(f"{name}: operator skipped - nothing recorded")
