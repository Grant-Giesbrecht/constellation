""" Hardware verification for the oscilloscope category.

Run against an instrument on the bench:

	pytest tests/hardware --driver=RigolDS1000Z --address=TCPIP0::192.168.1.74::INSTR
	pytest tests/hardware --driver=RigolDS1000Z --address=... --confirm

The first form is round-trip mode: set a parameter, read it back, check they agree. Fast and
unattended, and it earns a `roundtrip` record.

The second is moderated mode. It pauses after each check and asks a human to look at the
instrument, because **a round trip can be self-consistently wrong**: if a driver's setter writes
the timebase and its getter also reads the timebase, then setting volts/div round-trips perfectly
while the driver is driving the wrong control. No amount of automation catches that - which is why
every prompt below names the *physical* thing to look at and the control it would most plausibly
be confused with. Passing moderated mode earns a `confirmed` record.

For action commands (run/stop acquisition, single trigger, force trigger) there is no read-back at
all, so round-trip mode is not merely weaker for them - it is unreachable, and `confirmed` is the
only status they can ever hold.

Results are written to the driver's verification.yaml at the end of the run. See
docs/hardware_verification.md.
"""

import pytest

from constellation.instrument_control.oscilloscope.oscilloscope_ctg import Oscilloscope, MeasurementsMixin
from constellation.verification import VerificationStatus

from hardware_support import Check, already_confirmed, close_enough, requires_category, run_check, skip_if_unavailable

pytestmark = pytest.mark.hardware

@pytest.fixture(autouse=True)
def _category(instrument):
	''' A run has one instrument on the bench but collects every category module, so a module that
	is not about that instrument steps aside rather than failing. '''

	requires_category(instrument, Oscilloscope)

def _axis(wave, names):
	''' Pulls one axis out of a waveform dict, tolerating the key names in use.

	The category seeds `waveform` as {"time_S", "volt_V"} while RigolDS1000Z returns
	{"time_s", "volt_V", "channel"} - the data-shape contract is a known open item (P11). This
	test verifies the driver, not the naming, so it accepts either rather than failing a working
	instrument over a capital S.
	'''

	for name in names:
		if isinstance(wave, dict) and wave.get(name) is not None and len(wave[name]):
			return wave[name]

	return None

def _if_available(instrument, name, *args):
	''' Calls a setup method if the driver supports it, and reports whether it ran.

	Setup is not the thing under test: a DS1000E that cannot start an acquisition over SCPI should
	still have its waveform capture verified, so an unsupported setup step is a fact to work
	around rather than a reason to fail.
	'''

	if not instrument.feature_is_available(name):
		return None

	getattr(instrument, name)(*args)

	return True

# Every set/get pair the Oscilloscope category declares. Values are deliberately on the
# instrument's own quantization grid (1-2-5 for scales, whole divisions for offsets) so the
# round-trip tolerance can stay tight.
CHECKS = [

	Check(
		methods=("set_div_time", "get_div_time"),
		values=(1e-3, 5e-4),
		apply=lambda osc, ch, value: osc.set_div_time(value),
		read=lambda osc, ch: osc.get_div_time(),
		prompt=lambda osc, ch, value: (
			f"The HORIZONTAL timebase should now read {value*1e6:g} us/div - the readout across the "
			f"top of the screen, not the per-channel vertical scale at the bottom. If a channel's "
			f"VOLTS/DIV changed instead, the set/get pair agrees with itself while driving the "
			f"wrong control, and that is exactly what this question exists to catch."),
	),

	Check(
		methods=("set_offset_time", "get_offset_time"),
		values=(0.0, 2e-3),
		apply=lambda osc, ch, value: osc.set_offset_time(value),
		read=lambda osc, ch: osc.get_offset_time(),
		matches=lambda expected, actual: close_enough(expected, actual, abs_tol=1e-5),
		prompt=lambda osc, ch, value: (
			f"The horizontal POSITION (delay) should be {value*1e3:g} ms - the trigger marker at the "
			f"top of the screen has moved left of centre, while the timebase per division is "
			f"unchanged. If the timebase itself changed, the driver is writing :TIM:SCAL where it "
			f"should write :TIM:OFFS."),
	),

	Check(
		methods=("set_div_volt", "get_div_volt"),
		values=(0.5, 2.0),
		apply=lambda osc, ch, value: osc.set_div_volt(ch, value),
		read=lambda osc, ch: osc.get_div_volt(ch),
		prompt=lambda osc, ch, value: (
			f"Channel {ch}'s VERTICAL scale should read {value:g} V/div, in that channel's badge at "
			f"the bottom of the screen. Check the channel number too: a driver that ignores its "
			f"channel argument and always writes channel 1 round-trips perfectly."),
	),

	Check(
		methods=("set_offset_volt", "get_offset_volt"),
		values=(0.0, 1.0),
		apply=lambda osc, ch, value: osc.set_offset_volt(ch, value),
		read=lambda osc, ch: osc.get_offset_volt(ch),
		matches=lambda expected, actual: close_enough(expected, actual, abs_tol=0.05),
		prompt=lambda osc, ch, value: (
			f"Channel {ch}'s ground marker on the left edge should have moved to {value:g} V of "
			f"offset, WITHOUT the volts/div changing. If the trace got taller or shorter instead, "
			f"offset and scale are crossed."),
	),

	Check(
		methods=("set_chan_enable", "get_chan_enable"),
		values=(False, True),
		apply=lambda osc, ch, value: osc.set_chan_enable(ch, value),
		read=lambda osc, ch: osc.get_chan_enable(ch),
		matches=lambda expected, actual: bool(expected) == bool(actual),
		prompt=lambda osc, ch, value: (
			f"Channel {ch}'s trace should now be {'VISIBLE' if value else 'HIDDEN'} and its badge "
			f"{'lit' if value else 'dark'}. Watch that the OTHER channels are untouched - a driver "
			f"that writes the wrong channel number still round-trips if its getter reads the same "
			f"wrong channel."),
	),

	Check(
		methods=("set_coupling", "get_coupling"),
		values=(Oscilloscope.COUPLING_AC, Oscilloscope.COUPLING_DC),
		apply=lambda osc, ch, value: osc.set_coupling(ch, value),
		read=lambda osc, ch: osc.get_coupling(ch),
		matches=lambda expected, actual: expected == actual,
		prompt=lambda osc, ch, value: (
			f"Channel {ch}'s coupling indicator should read {'AC' if value == Oscilloscope.COUPLING_AC else 'DC'} "
			f"(the small symbol in the channel badge). With a DC-offset signal applied, AC coupling "
			f"visibly re-centres the trace - that is the check worth doing if the badge is hard to "
			f"read."),
	),

	Check(
		methods=("set_probe_attenuation", "get_probe_attenuation"),
		values=(1.0, 10.0),
		apply=lambda osc, ch, value: osc.set_probe_attenuation(ch, value),
		read=lambda osc, ch: osc.get_probe_attenuation(ch),
		prompt=lambda osc, ch, value: (
			f"Channel {ch}'s probe ratio should read {value:g}X. The visible side-effect is that the "
			f"volts/div readout scales by the same factor while the trace on screen does not move - "
			f"if the trace changed height, the driver wrote a vertical scale rather than the probe "
			f"ratio."),
	),

	Check(
		methods=("set_bandwidth_limit", "get_bandwidth_limit"),
		values=(False, True),
		apply=lambda osc, ch, value: osc.set_bandwidth_limit(ch, value),
		read=lambda osc, ch: osc.get_bandwidth_limit(ch),
		matches=lambda expected, actual: bool(expected) == bool(actual),
		prompt=lambda osc, ch, value: (
			f"Channel {ch}'s bandwidth-limit indicator (a 'B' or 'BW' in the channel badge) should be "
			f"{'ON' if value else 'OFF'}. With a fast-edged signal applied, switching it on visibly "
			f"rounds the edges."),
	),

	Check(
		methods=("set_trigger_mode", "get_trigger_mode"),
		values=(Oscilloscope.TRIG_AUTO, Oscilloscope.TRIG_NORM),
		apply=lambda osc, ch, value: osc.set_trigger_mode(value),
		read=lambda osc, ch: osc.get_trigger_mode(),
		matches=lambda expected, actual: expected == actual,
		prompt=lambda osc, ch, value: (
			f"The trigger MODE readout should read {'AUTO' if value == Oscilloscope.TRIG_AUTO else 'NORMAL'} "
			f"- top-right on most scopes. Do not confuse it with the run/stop state, which is a "
			f"different control that also lives up there."),
	),

	Check(
		methods=("set_trigger_level", "get_trigger_level"),
		values=(0.0, 0.5),
		apply=lambda osc, ch, value: osc.set_trigger_level(value),
		read=lambda osc, ch: osc.get_trigger_level(),
		matches=lambda expected, actual: close_enough(expected, actual, abs_tol=0.05),
		prompt=lambda osc, ch, value: (
			f"The trigger LEVEL marker on the right edge should sit at {value:g} V, and the level "
			f"readout should agree. If the channel's offset marker moved instead, level and offset "
			f"are crossed - both move a marker vertically, which is what makes this pair worth "
			f"looking at rather than trusting."),
	),
]

def _trigger_source_check(osc, ch):
	''' Built per-run because the expected value is whatever _format_trigger_source() produces for
	this channel - the category's own encoding, not a literal to duplicate here. '''

	expected = osc._format_trigger_source(channel=ch)

	return Check(
		methods=("set_trigger_source", "get_trigger_source"),
		values=(expected,),
		apply=lambda osc, ch, value: osc.set_trigger_source(channel=ch),
		read=lambda osc, ch: osc.get_trigger_source(),
		matches=lambda expected, actual: expected == actual,
		prompt=lambda osc, ch, value: (
			f"The trigger SOURCE readout should name channel {ch}. If it names a different channel, "
			f"or still says EXT/LINE, the driver's source encoding is wrong in a way the round trip "
			f"cannot see - its getter parses back whatever its setter wrote."),
	)

@pytest.mark.parametrize("check", CHECKS, ids=[c.id for c in CHECKS])
def test_roundtrip(check, instrument, channel, recorder, confirm, moderated, request):
	""" Set a parameter, read it back, and (in moderated mode) have a human confirm the instrument
	physically did it. """

	run_check(check, instrument, channel, recorder, confirm, moderated, request.config.getoption("--recheck"))

def test_trigger_source(instrument, channel, recorder, confirm, moderated, request):

	run_check(_trigger_source_check(instrument, channel), instrument, channel, recorder, confirm, moderated, request.config.getoption("--recheck"))

# Action commands. There is nothing to read back, so these are unreachable in round-trip mode and
# `confirmed` is the only status they can ever hold - the prompt is not a nicety here, it is the
# entire test.
ACTIONS = {
	"run_acquisition": "The scope should now be RUNNING - the trace is updating and the run/stop indicator is green/RUN.",
	"stop_acquisition": "The scope should now be STOPPED - the trace is frozen and the run/stop indicator is red/STOP.",
	"do_single_trigger": "The scope should have armed for a SINGLE acquisition: the indicator reads SINGLE/WAIT, and it captures exactly one sweep and then stops.",
	"do_force_trigger": "The scope should have taken one acquisition immediately, even with no qualifying trigger event - the trace refreshed once.",
}

@pytest.mark.parametrize("name", sorted(ACTIONS), ids=sorted(ACTIONS))
def test_action_command(name, instrument, recorder, confirm, moderated, request):

	driver_cls = type(instrument)

	skip_if_unavailable(driver_cls, [name])

	if not moderated:
		pytest.skip(f"{name}() has no read-back, so round-trip mode cannot verify it - run with --confirm")

	if not request.config.getoption("--recheck") and already_confirmed(driver_cls, [name], model=recorder.model, idn=recorder.idn):
		pytest.skip(f"already confirmed on {recorder.model or 'this model'} - pass --recheck to re-run")

	try:
		getattr(instrument, name)()
	except Exception as e:
		recorder.record(name, VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
		raise

	answer = confirm(ACTIONS[name])

	if answer is False:
		recorder.record(name, VerificationStatus.FAILED, note="operator reported the instrument did not do this")
		pytest.fail(f"{name}: operator reported the instrument did not do what was asked")

	if answer is True:
		recorder.record(name, VerificationStatus.CONFIRMED)
	else:
		pytest.skip(f"{name}: operator skipped - nothing recorded")

def test_get_waveform(instrument, channel, recorder, confirm, moderated):
	""" A waveform is data, not a setting, so 'round trip' means: the capture is well-formed, and
	its x-axis matches the timebase the driver believes is set. That second half is what catches a
	preamble misparse, which a length check alone sails straight past. """

	driver_cls = type(instrument)

	skip_if_unavailable(driver_cls, ["get_waveform"])

	# Setup uses whatever this instrument actually supports. A DS1000E cannot set its timebase or
	# start an acquisition over SCPI, and none of that stops it returning a waveform - so the
	# setup steps are optional and only the capture itself is the test.
	_if_available(instrument, "set_chan_enable", channel, True)
	timebase = 1e-3
	if _if_available(instrument, "set_div_time", timebase) is None:
		timebase = instrument.state.div_time
	_if_available(instrument, "run_acquisition")

	try:
		wave = instrument.get_waveform(channel)
	except Exception as e:
		recorder.record("get_waveform", VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
		raise

	volts = _axis(wave, ("volt_V", "y"))
	times = _axis(wave, ("time_s", "time_S", "x"))

	if volts is None or len(volts) < 2:
		recorder.record("get_waveform", VerificationStatus.FAILED, note=f"returned {type(wave).__name__} with no usable voltage axis")
		pytest.fail(f"get_waveform returned nothing usable: {wave!r}")

	# The captured span should cover the screen: ndiv_horiz divisions at the timebase in force. If
	# the timebase is unknown (an instrument that cannot report it), there is nothing to compare
	# against and the check is skipped rather than guessed at.
	span = (max(times) - min(times)) if times is not None and len(times) > 1 else None
	expected_span = instrument.state.ndiv_horiz * timebase if timebase else None

	if span is not None and expected_span and not close_enough(expected_span, span, rel=0.25):
		recorder.record("get_waveform", VerificationStatus.FAILED, note=f"x-axis spans {span:g} s, expected about {expected_span:g} s")
		pytest.fail(f"get_waveform x-axis spans {span:g} s, expected about {expected_span:g} s - check the preamble parse")

	answer = confirm(
		f"Compare the captured waveform against what is on the screen for channel {channel}: same "
		f"shape, same amplitude, same number of periods across the width. A y-axis that is right in "
		f"shape but wrong in scale means the preamble's yincrement/yorigin/yreference are misapplied "
		f"- which no length or span check can see.")

	if answer is False:
		recorder.record("get_waveform", VerificationStatus.FAILED, note="operator reported the capture did not match the display")
		pytest.fail("get_waveform: operator reported the capture did not match the display")

	recorder.record("get_waveform", VerificationStatus.CONFIRMED if answer else VerificationStatus.ROUNDTRIP)

# --- Measurements mixin ---------------------------------------------------------------------
#
# Only drivers that inherit MeasurementsMixin have these at all, so they are skipped rather than
# failed elsewhere - a DS1000E is not broken for lacking them.

def _requires_measurements(instrument):

	if not isinstance(instrument, MeasurementsMixin):
		pytest.skip(f"{type(instrument).__name__} does not implement MeasurementsMixin")

def test_measurements(instrument, channel, recorder, confirm, moderated):

	_requires_measurements(instrument)

	driver_cls = type(instrument)
	methods = ("clear_measurements", "add_measurement", "get_measurement")

	skip_if_unavailable(driver_cls, methods)

	instrument.set_chan_enable(channel, True)
	instrument.run_acquisition()

	try:
		instrument.clear_measurements()
		instrument.add_measurement(channel, MeasurementsMixin.MEAS_VPP)
		value = instrument.get_measurement(channel, MeasurementsMixin.MEAS_VPP)
	except Exception as e:
		recorder.record(methods, VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
		raise

	if value is None:
		recorder.record(methods, VerificationStatus.FAILED, note="get_measurement returned None")
		pytest.fail("get_measurement returned None")

	answer = confirm(
		f"The scope should be displaying a Vpp measurement for channel {channel}, and it should read "
		f"about {value:g} V. Check the CHANNEL as well as the number - a driver that always measures "
		f"channel 1 returns a perfectly plausible value.")

	if answer is False:
		recorder.record(methods, VerificationStatus.FAILED, note="operator reported the measurement was wrong or on the wrong channel")
		pytest.fail("measurements: operator reported the measurement was wrong or on the wrong channel")

	recorder.record(methods, VerificationStatus.CONFIRMED if answer else VerificationStatus.ROUNDTRIP)

@pytest.mark.parametrize("enable", [True, False], ids=["on", "off"])
def test_measurement_stat_display(enable, instrument, recorder, confirm, moderated):

	_requires_measurements(instrument)

	driver_cls = type(instrument)
	methods = ("set_measurement_stat_display", "get_measurement_stat_display")

	skip_if_unavailable(driver_cls, methods)

	try:
		instrument.set_measurement_stat_display(enable)
		readback = instrument.get_measurement_stat_display()
	except Exception as e:
		recorder.record(methods, VerificationStatus.FAILED, note=f"raised {type(e).__name__}: {e}")
		raise

	if bool(readback) != bool(enable):
		recorder.record(methods, VerificationStatus.FAILED, note=f"set {enable!r}, read back {readback!r}")
		pytest.fail(f"set_measurement_stat_display({enable!r}) read back {readback!r}")

	answer = confirm(
		f"The measurement STATISTICS table should now be {'VISIBLE' if enable else 'HIDDEN'} - the "
		f"block showing max/min/average per measurement, not the single-line measurement readout "
		f"itself.")

	if answer is False:
		recorder.record(methods, VerificationStatus.FAILED, note="operator reported the statistics table did not change")
		pytest.fail("measurement stat display: operator reported the statistics table did not change")

	recorder.record(methods, VerificationStatus.CONFIRMED if answer else VerificationStatus.ROUNDTRIP)
