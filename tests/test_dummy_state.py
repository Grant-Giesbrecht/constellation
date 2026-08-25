""" Tests for the Dummy-mode system and the InstrumentState tracking/updating/saving system.

Several tests below are marked `xfail(strict=True)`: they assert the *correct/desired* behavior
of a currently-confirmed bug, not the current (buggy) behavior. `strict=True` means pytest will
report an XPASS (test failure) the moment someone fixes the underlying bug without also updating
the test - that's intentional, it's the signal to remove the xfail marker. See
docs/dummy_and_state_review.md for the full writeup of each bug.
"""

import os
import tempfile
import pytest
import pylogfile.base as plf

from constellation.base import InstrumentState, IndexedList, Driver, CheckOnline
from constellation.relay import DirectSCPIRelay, VICPDirectSCPIRelay, CommandRelay
from constellation.all import (RigolDS1000Z as _RZ, SiglentSSA3000X, RigolDP832, SiglentSDM3000X,
	Keysight34400, Keithley2700, RohdeSchwarzZVA, SiglentSDG2000X, RohdeSchwarzFSE,
	DigitalMultimeter)
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import Oscilloscope, OscilloscopeChannelState
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000Z_dvr import RigolDS1000Z

def make_log():
	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL  # keep test output quiet
	return log

def make_dummy_osc(address="TCPIP0::10.0.0.9::INSTR", **kwargs):
	""" A RigolDS1000Z in dummy mode with an explicit, fresh relay - deliberately never relying
	on the buggy shared-default relay (see test_relay_default_argument_is_shared_bug) so other
	tests stay isolated from it. """
	return RigolDS1000Z(address, log=make_log(), relay=DirectSCPIRelay(), dummy=True, **kwargs)

# ---------------------------------------------------------------------------
# IndexedList
# ---------------------------------------------------------------------------

def test_indexed_list_iteration_is_reentrant():
	""" Regression test: __iter__ used to mutate a shared self._iter_index, which broke nested
	iteration over the same IndexedList (already fixed this session - pins the fix down). """

	il = IndexedList(1, 4)
	il.set_idx_val(1, "a")
	il.set_idx_val(3, "c")

	outer_seen = []
	for outer in il:
		inner_seen = list(il)  # nested iteration over the SAME object
		outer_seen.append(outer)
		assert inner_seen == ["a", "c"]

	assert outer_seen == ["a", "c"]

def test_indexed_list_populated_items():
	il = IndexedList(1, 4)
	il.set_idx_val(2, "b")
	il.set_idx_val(4, "d")

	assert list(il.populated_items()) == [(2, "b"), (4, "d")]
	assert list(il) == ["b", "d"]
	assert il.get_populated() == [2, 4]

def test_indexed_list_out_of_range_raises_keyerror():
	il = IndexedList(1, 4)
	with pytest.raises(KeyError):
		il.set_idx_val(0, "x")
	with pytest.raises(KeyError):
		il.set_idx_val(5, "x")

# ---------------------------------------------------------------------------
# InstrumentState.set()/get()
# ---------------------------------------------------------------------------

class _DemoState(InstrumentState):
	__state_fields__ = ("volt", "channels")

	def __init__(self, log=None):
		super().__init__(log=log)
		self.add_param("volt", unit="V", value=1.0)
		self.add_param("channels", unit="", value=IndexedList(1, 2))
		self.channels[1] = _DemoChannelState(log=log)
		self.validate()

class _DemoChannelState(InstrumentState):
	__state_fields__ = ("gain",)

	def __init__(self, log=None):
		super().__init__(log=log)
		self.add_param("gain", unit="", value=0.0)
		self.validate()

def test_instrumentstate_set_get_scalar():
	s = _DemoState(log=make_log())
	assert s.set(["volt"], 5.0) is True
	assert s.get(["volt"]) == 5.0

def test_instrumentstate_set_get_indexed_nested():
	s = _DemoState(log=make_log())
	assert s.set(["channels", "gain"], 3.5, indices=[1]) is True
	assert s.get(["channels", "gain"], indices=[1]) == 3.5
	assert s.channels.get_idx_val(1).gain == 3.5

def test_instrumentstate_set_missing_param_returns_false_not_raise():
	""" Sanity check: an invalid (non-fragment) param path fails gracefully, unlike the fragment
	path below - this is the behavior the fragment path should also have. """
	s = _DemoState(log=make_log())
	assert s.set(["does_not_exist"], 1.0) is False
	assert s.get(["does_not_exist"]) is None

# FIXED: set()/get() now share _resolve() and _get_fragment(); the fragment error no
# longer references an unassigned local.
def test_instrumentstate_set_bad_fragment_fails_gracefully():
	s = _DemoState(log=make_log())
	result = s.set(["volt"], 1.0, fragment="no_such_fragment")
	assert result is False

# FIXED: InstrumentState.get() now takes fragment=, mirroring set().
def test_instrumentstate_get_supports_fragment_like_set_does():
	s = _DemoState(log=make_log())
	s.state_fragments["extra"] = _DemoChannelState(log=make_log())
	s.set(["gain"], 9.0, fragment="extra")
	# This call raises TypeError today because get() doesn't accept `fragment=` at all.
	assert s.get(["gain"], fragment="extra") == 9.0

# ---------------------------------------------------------------------------
# Dummy mode: Driver/category-level set_*/get_* behavior
# ---------------------------------------------------------------------------

def test_dummy_connect_succeeds_without_hardware():
	osc = make_dummy_osc()
	assert osc.dummy is True
	assert osc.online is True

def test_dummy_set_div_volt_persists_to_state():
	""" set_div_volt is NOT decorated with @enabledummy at the category level, so it goes through
	the normal modify_state() dummy short-circuit and correctly persists - this is the behavior
	every dummy-mode setter should have (contrast with the trigger/attenuation tests below). """
	osc = make_dummy_osc()
	osc.set_div_volt(1, 2.0)
	assert osc.state.channels[1].div_volt == 2.0
	assert osc.get_div_volt(1) == 2.0

def test_dummy_set_chan_enable_persists_to_state():
	osc = make_dummy_osc()
	osc.set_chan_enable(1, True)
	assert osc.state.channels[1].chan_en is True

# FIXED: @enabledummy removed from the state-mapped setters; they now flow through
# modify_state(), whose dummy branch stores the value.
def test_dummy_set_trigger_mode_persists_to_state():
	osc = make_dummy_osc()
	osc.set_trigger_mode(Oscilloscope.TRIG_AUTO)
	assert osc.state.trigger_mode == Oscilloscope.TRIG_AUTO

# FIXED: see test_dummy_set_trigger_mode_persists_to_state.
def test_dummy_set_trigger_level_persists_to_state():
	osc = make_dummy_osc()
	osc.set_trigger_level(1.5)
	assert osc.state.trigger_level == 1.5

# FIXED: see test_dummy_set_trigger_mode_persists_to_state.
def test_dummy_set_probe_attenuation_persists_to_state():
	osc = make_dummy_osc()
	osc.set_probe_attenuation(1, 10)
	assert osc.state.channels[1].attenuation == 10

def test_dummy_waveform_reflects_channel_settings():
	""" remake_dummy_waves() should clip the generated waveform to the configured div_volt/
	offset_volt window - a basic sanity check that dummy waveform generation responds to state. """
	osc = make_dummy_osc()
	osc.set_div_volt(1, 1.0)
	osc.set_offset_volt(1, 0.0)
	wf = osc.get_waveform(1)
	v_span = osc.state.ndiv_vert * 1.0
	v_min, v_max = -v_span / 2, v_span / 2
	assert all(v_min - 1e-9 <= v <= v_max + 1e-9 for v in wf["volt_V"])

# ---------------------------------------------------------------------------
# Mutable default argument bug
# ---------------------------------------------------------------------------

# FIXED: no driver declares a relay instance as a signature default any more. Every driver now
# takes `relay:CommandRelay=None` and Driver.__init__ constructs a fresh DirectSCPIRelay() when
# none is supplied, so instances can't share one.
def test_relay_default_argument_is_not_shared_between_instances():
	log = make_log()
	osc1 = RigolDS1000Z("TCPIP0::10.0.0.1::INSTR", log=log, dummy=True)
	osc2 = RigolDS1000Z("TCPIP0::10.0.0.2::INSTR", log=log, dummy=True)

	assert osc1.relay is not osc2.relay
	assert osc1.relay.address == "TCPIP0::10.0.0.1::INSTR"

# ---------------------------------------------------------------------------
# check_online() ignoring is_scpi
# ---------------------------------------------------------------------------

class _AlwaysRespondsRelay(DirectSCPIRelay):
	""" Stands in for a relay attached to a non-SCPI instrument that nonetheless returns
	something for any query - used to prove check_online() queries hardware even when it has
	just declared that it won't. """

	def query(self, cmd):
		return True, "some-non-scpi-response"

@pytest.mark.xfail(strict=True, reason=(
	"BUG: Driver.check_online()'s CheckOnline.AUTO branch warns 'Cannot use CheckOnline.AUTO "
	"for non-SCPI instruments. Defaulting to OFFLINE.' and sets self.online = False, but then "
	"falls through (no return/elif) and unconditionally calls self.relay.query('*IDN?') anyway, "
	"immediately overwriting self.online based on that query's result - defeating the guard "
	"and querying hardware that was just declared unable to handle SCPI."
))
def test_check_online_skips_query_for_non_scpi_instrument():
	osc = make_dummy_osc()
	osc.is_scpi = False
	osc.relay = _AlwaysRespondsRelay()
	osc.check_online_on_error = CheckOnline.AUTO
	osc.check_online()
	assert osc.online is False

# ---------------------------------------------------------------------------
# State save/load (dump_state / restore_state)
# ---------------------------------------------------------------------------

def test_dump_and_restore_state_roundtrip_scalars(tmp_path):
	osc = make_dummy_osc()
	osc.set_div_time(0.002)
	osc.set_chan_enable(1, True)
	osc.set_div_volt(1, 0.5)

	fn = str(tmp_path / "state.hdf")
	assert osc.dump_state(fn) is True
	assert os.path.exists(fn)

	osc2 = make_dummy_osc()
	assert osc2.restore_state(fn) is True
	assert osc2.state.div_time == 0.002
	assert osc2.state.channels[1].chan_en == True
	assert osc2.state.channels[1].div_volt == 0.5

@pytest.mark.xfail(strict=True, reason=(
	"BUG: values round-tripped through dump_state()/restore_state() (HDF via h5py) come back as "
	"numpy scalar types instead of the original Python types - a Python bool becomes numpy.bool_, "
	"float becomes numpy.float64, int becomes numpy.int64. Values still compare equal with `==`, "
	"but `isinstance(x, bool)`/`type(x) is float` checks elsewhere (or strict JSON re-serialization) "
	"would behave differently after a restore than before one."
))
def test_dump_restore_preserves_native_python_types():
	osc = make_dummy_osc()
	osc.set_chan_enable(1, True)
	osc.set_div_time(0.002)

	fn = tempfile.mktemp(suffix=".hdf")
	try:
		osc.dump_state(fn)
		osc2 = make_dummy_osc()
		osc2.restore_state(fn)
		assert type(osc2.state.channels[1].chan_en) is bool
		assert type(osc2.state.div_time) is float
	finally:
		if os.path.exists(fn):
			os.remove(fn)

@pytest.mark.xfail(strict=True, reason=(
	"BUG: OscilloscopeState.channel_colors is a plain dict keyed by integer channel numbers. "
	"stardust.dict_to_hdf's write_level() creates an HDF group per dict, then h5py.Group."
	"create_group()/create_dataset() raise TypeError for non-string names - so every entry in "
	"channel_colors silently fails to write. The failure is swallowed: write_level() doesn't "
	"propagate nested calls' return values, and dict_to_hdf() returns True even on its own "
	"'failed' branch - so dump_state() reports success (True) while the file's channel_colors "
	"group ends up completely empty. Confirmed by inspecting the written HDF file directly."
))
def test_dump_state_preserves_channel_colors(tmp_path):
	osc = make_dummy_osc()
	fn = str(tmp_path / "state.hdf")
	osc.dump_state(fn)

	osc2 = make_dummy_osc()
	osc2.restore_state(fn)
	assert osc2.state.channel_colors == osc.state.channel_colors
	assert len(osc2.state.channel_colors) == 4

@pytest.mark.xfail(strict=True, reason=(
	"BUG: Driver.state_to_dict()'s `include_data` parameter is accepted but never referenced in "
	"the function body - self.data (DataEntry values) is never included in the output regardless "
	"of the flag, contradicting the docstring ('Optional argument to include instrument data "
	"state as well')."
))
def test_state_to_dict_include_data_flag_has_effect():
	osc = make_dummy_osc()
	osc.data["some_measurement"] = "placeholder-value"

	without_data = osc.state_to_dict(include_data=False)
	with_data = osc.state_to_dict(include_data=True)

	assert "data" not in without_data
	assert with_data.get("data") == {"some_measurement": "placeholder-value"}

# ---------------------------------------------------------------------------
# CommandRelay read/query return values
# ---------------------------------------------------------------------------

class _FakeVisaInstrument:
	""" Stands in for a pyvisa Resource / pyvicp Client so the relays can be exercised with no
	hardware. Records writes and replays a canned reply for reads. """

	def __init__(self, reply="CANNED-REPLY"):
		self.reply = reply
		self.written = []
		self.timeout = None
		self.read_termination = None
		self.write_termination = None

	# DirectSCPIRelay (pyvisa) surface
	def write(self, cmd): self.written.append(cmd)
	def read(self): return self.reply
	def query(self, cmd): self.written.append(cmd); return self.reply

	# VICPDirectSCPIRelay (pyvicp) surface
	def send(self, data): self.written.append(data.decode())
	def receive(self): return self.reply.encode()

def _wire_relay(relay, reply="CANNED-REPLY"):
	""" Attaches a fake instrument to an already-constructed relay, bypassing connect(). """
	relay.configure("FAKE::ADDR", make_log())
	relay.inst = _FakeVisaInstrument(reply)
	return relay

def test_direct_relay_read_returns_the_value_it_read():
	""" Regression: DirectSCPIRelay.read() read into `rv` then returned a hardcoded empty
	string, so every Driver.read() got "" no matter what the instrument sent. """

	relay = _wire_relay(DirectSCPIRelay(), reply="3.14")
	assert relay.read() == (True, "3.14")

def test_direct_relay_query_returns_the_value_it_read():
	relay = _wire_relay(DirectSCPIRelay(), reply="3.14")
	assert relay.query("*IDN?") == (True, "3.14")
	assert relay.inst.written == ["*IDN?"]

def test_vicp_relay_read_returns_the_value_it_read():
	""" Regression: VICPDirectSCPIRelay.read() had the same discard-the-value bug. """

	relay = _wire_relay(VICPDirectSCPIRelay(), reply="2.71")
	assert relay.read() == (True, "2.71")

def test_vicp_relay_query_returns_the_value_it_read():
	""" Regression: VICPDirectSCPIRelay.query() discarded its value too. Since query() is how
	every category getter reads hardware, this meant the entire LeCroy/VICP path returned empty
	strings for every parameter. """

	relay = _wire_relay(VICPDirectSCPIRelay(), reply="LECROY,WR44XI")
	assert relay.query("*IDN?") == (True, "LECROY,WR44XI")
	assert relay.inst.written == ["*IDN?"]

def test_vicp_relay_init_names_the_attribute_its_methods_use():
	""" Regression: __init__ set self.instr while every method used self.inst. """

	relay = VICPDirectSCPIRelay()
	assert hasattr(relay, "inst")
	assert not hasattr(relay, "instr")

# ---------------------------------------------------------------------------
# relay= plumbing across every migrated driver
# ---------------------------------------------------------------------------

# RigolDS1000E is excluded: it doesn't implement several Oscilloscope abstract methods yet, so it
# can't be instantiated at all. That's a separate, pre-existing gap (see todo_list.md).
ALL_DRIVERS = [
	RigolDS1000Z, SiglentSSA3000X, RigolDP832, SiglentSDM3000X, Keysight34400,
	Keithley2700, RohdeSchwarzZVA, SiglentSDG2000X, RohdeSchwarzFSE,
]

@pytest.mark.parametrize("driver_cls", ALL_DRIVERS, ids=lambda c: c.__name__)
def test_driver_does_not_share_a_default_relay(driver_cls):
	""" Two instruments built without an explicit relay= must not share one relay object, or the
	second one's address silently clobbers the first's. """

	a = driver_cls("ADDR-A", make_log(), dummy=True)
	b = driver_cls("ADDR-B", make_log(), dummy=True)

	assert a.relay is not b.relay
	assert a.relay.address == "ADDR-A"
	assert b.relay.address == "ADDR-B"

@pytest.mark.parametrize("driver_cls", ALL_DRIVERS, ids=lambda c: c.__name__)
def test_driver_accepts_an_injected_relay(driver_cls):
	""" Every driver must accept relay=, or it can never be driven over labmesh - that swap is
	the entire point of the CommandRelay abstraction. Several drivers used to hard-code
	relay=DirectSCPIRelay() inside super().__init__(), which raised TypeError (multiple values
	for 'relay') if a caller tried. """

	injected = DirectSCPIRelay()
	dvr = driver_cls("ADDR", make_log(), relay=injected, dummy=True)
	assert dvr.relay is injected

# ---------------------------------------------------------------------------
# Dummy dispatch: one mechanism (modify_state), @enabledummy for synthetic only
# ---------------------------------------------------------------------------

import inspect as _inspect
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import MeasurementsMixin

# The ONLY methods allowed to keep @enabledummy: dummy mode must invent a value that isn't
# already tracked in state, or the method is a pure hardware action with no state at all.
# Anything else must go through modify_state(), which handles dummy generically. This test is
# the guard rail - it fails if someone decorates a plain setter/getter again (the exact mistake
# that made 5 oscilloscope setters silently no-op in dummy mode).
ALLOWED_ENABLEDUMMY = {
	"Oscilloscope": {"get_waveform", "run_acquisition", "stop_acquisition",
	                 "do_single_trigger", "do_force_trigger"},
	"PowerSupply": {"get_measured_output"},
	"BasicVectorNetworkAnalyzerCtg": {"get_trace_data"},
}

def _enabledummy_methods(cls):
	""" Names of methods on cls (defined by cls itself) wrapped by @enabledummy. The decorator
	returns a plain closure named 'wrapper', which is what we detect. """
	found = set()
	for name, obj in vars(cls).items():
		fn = getattr(obj, "__func__", obj)
		if callable(fn) and getattr(fn, "__name__", None) == "wrapper":
			found.add(name)
	return found

@pytest.mark.parametrize("cls_name", sorted(ALLOWED_ENABLEDUMMY))
def test_enabledummy_only_on_synthetic_methods(cls_name):
	import constellation.all as ca
	cls = getattr(ca, cls_name)
	assert _enabledummy_methods(cls) == ALLOWED_ENABLEDUMMY[cls_name]

def test_no_category_hand_maintains_a_getter_table():
	""" The AWG/DMM/SpectrumAnalyzer dummy_responder overrides were pure state read-back tables,
	duplicating what modify_state() now does generically. They should stay deleted. """
	import constellation.all as ca
	for cls_name in ("ArbitraryWaveformGenerator", "DigitalMultimeter", "SpectrumAnalyzer"):
		cls = getattr(ca, cls_name)
		assert "dummy_responder" not in vars(cls), (
			f"{cls_name} re-added a dummy_responder override - if it's a plain state read-back, "
			f"modify_state() already handles it")

@pytest.mark.parametrize("setter,getter,value", [
	("set_trigger_mode",      "get_trigger_mode",      Oscilloscope.TRIG_SINGLE),
	("set_trigger_level",     "get_trigger_level",     0.75),
	("set_div_time",          "get_div_time",          5e-3),
	("set_offset_time",       "get_offset_time",       1e-3),
])
def test_dummy_scalar_setter_roundtrips(setter, getter, value):
	""" Every scalar set_*/get_* pair must round-trip through state in dummy mode with no
	dummy_responder case backing it. """
	osc = make_dummy_osc()
	getattr(osc, setter)(value)
	assert getattr(osc, getter)() == value

@pytest.mark.parametrize("setter,getter,value", [
	("set_div_volt",          "get_div_volt",          0.25),
	("set_offset_volt",       "get_offset_volt",       -0.1),
	("set_chan_enable",       "get_chan_enable",       False),
	("set_coupling",          "get_coupling",          Oscilloscope.COUPLING_AC),
	("set_probe_attenuation", "get_probe_attenuation", 10),
	("set_bandwidth_limit",   "get_bandwidth_limit",   True),
])
def test_dummy_per_channel_setter_roundtrips(setter, getter, value):
	osc = make_dummy_osc()
	getattr(osc, setter)(2, value)
	assert getattr(osc, getter)(2) == value

def test_dummy_trigger_source_roundtrips():
	""" set_trigger_source takes (channel/external/line) rather than a plain value, so it needs
	its own case. It was one of the silently-no-op setters. """
	osc = make_dummy_osc()
	osc.set_trigger_source(channel=2)
	assert osc.state.trigger_source == "CHAN2"
	assert osc.get_trigger_source() == "CHAN2"

def test_dummy_getter_does_not_clobber_state_with_none():
	""" The regression the modify_state dummy branch prevents: a get_* in dummy mode used to
	write self._super_hint (None, since the driver body never ran) straight into state. """
	osc = make_dummy_osc()
	osc.set_div_volt(1, 0.5)
	for _ in range(3):
		assert osc.get_div_volt(1) == 0.5
	assert osc.state.channels[1].div_volt == 0.5

# --- mixins, which previously had no dummy support at all ------------------------------

def test_dummy_add_and_clear_measurements():
	osc = make_dummy_osc()
	assert osc.add_measurement(1, MeasurementsMixin.MEAS_VPP) is True
	assert osc.add_measurement(2, MeasurementsMixin.MEAS_VMAX) is True
	# adding the same measurement twice is rejected
	assert osc.add_measurement(1, MeasurementsMixin.MEAS_VPP) is False

	frag = osc.state.state_fragments["measurements"]
	assert len(list(frag.active_measurements)) == 2

	osc.clear_measurements()
	assert list(frag.active_measurements) == []

def test_dummy_measurement_is_consistent_with_the_dummy_waveform():
	""" Dummy measurements are synthesized from the same waveform get_waveform() returns, so
	they have to agree with it rather than being a fixed sentinel. """
	osc = make_dummy_osc()
	osc.set_div_volt(1, 1.0)
	osc.add_measurement(1, MeasurementsMixin.MEAS_VPP)
	osc.add_measurement(1, MeasurementsMixin.MEAS_VMAX)

	wf = osc.get_waveform(1)
	expected_vpp = max(wf["volt_V"]) - min(wf["volt_V"])

	assert osc.get_measurement(1, MeasurementsMixin.MEAS_VPP) == pytest.approx(expected_vpp)
	assert osc.get_measurement(1, MeasurementsMixin.MEAS_VMAX) == pytest.approx(max(wf["volt_V"]))

def test_dummy_measurement_records_last_measured_value():
	osc = make_dummy_osc()
	osc.add_measurement(1, MeasurementsMixin.MEAS_VPP)
	value = osc.get_measurement(1, MeasurementsMixin.MEAS_VPP)

	frag = osc.state.state_fragments["measurements"]
	stored = [m.last_measured_value for m in frag.active_measurements]
	assert stored == [value]

def test_dummy_measurement_not_added_returns_none():
	osc = make_dummy_osc()
	assert osc.get_measurement(1, MeasurementsMixin.MEAS_FREQ) is None

def test_dummy_stat_display_roundtrips_through_a_state_fragment():
	""" Exercises modify_state()'s fragment= path in dummy mode, which needed
	InstrumentState.get(fragment=) to exist. """
	osc = make_dummy_osc()
	osc.set_measurement_stat_display(True)
	assert osc.get_measurement_stat_display() is True
	osc.set_measurement_stat_display(False)
	assert osc.get_measurement_stat_display() is False

# --- the other categories, which lost their dummy_responder entirely -------------------

def test_dummy_power_supply_roundtrips_and_synthesizes_measurements():
	psu = RigolDP832("dummy", make_log(), dummy=True)
	psu.set_voltage(2, 3.3)
	psu.set_current(2, 0.25)
	psu.set_output_enable(2, True)

	assert psu.get_voltage(2) == 3.3
	assert psu.get_output_enable(2) is True

	# measured V/I are synthetic (setpoint + noise), so they must be near - not equal to - the
	# setpoint, and must actually vary between reads.
	v1, i1 = psu.get_measured_output(2)
	assert abs(v1 - 3.3) < 0.2
	assert (v1, i1) != psu.get_measured_output(2)

def test_dummy_awg_roundtrips_without_a_dummy_responder():
	awg = SiglentSDG2000X("dummy", make_log(), dummy=True)
	awg.set_frequency(1, 2.5e3)
	awg.set_amplitude(1, 0.8)
	awg.set_output_enable(1, True)

	assert awg.get_frequency(1) == 2.5e3
	assert awg.get_amplitude(1) == 0.8
	assert awg.get_output_enable(1) is True

def test_dummy_dmm_roundtrips_without_a_dummy_responder():
	dmm = SiglentSDM3000X("dummy", make_log(), dummy=True)
	dmm.set_measurement(DigitalMultimeter.MEAS_CURR_DC)
	assert dmm.get_measurement() == DigitalMultimeter.MEAS_CURR_DC
	dmm.set_trigger_type(DigitalMultimeter.TRIG_SINGLE)
	assert dmm.get_trigger_type() == DigitalMultimeter.TRIG_SINGLE

def test_dummy_spectrum_analyzer_roundtrips_without_a_dummy_responder():
	sa = SiglentSSA3000X("dummy", make_log(), dummy=True)
	sa.set_freq_start(1e9)
	sa.set_freq_end(2e9)
	sa.set_ref_level(-10)
	assert sa.get_freq_start() == 1e9
	assert sa.get_freq_end() == 2e9
	assert sa.get_ref_level() == -10

# ---------------------------------------------------------------------------
# superreturn: driver return value -> _super_hint -> category state tracking
# ---------------------------------------------------------------------------

class _CannedRelay(CommandRelay):
	""" A relay that replays canned SCPI responses, so tests can exercise the REAL (non-dummy)
	driver bodies - the code path dummy-mode tests never touch. """

	def __init__(self, table=None):
		super().__init__()
		self.table = {"*IDN?": "RIGOL TECHNOLOGIES,DS1054Z,X,1.0"}
		self.table.update(table or {})
		self.sent = []
		self.query_count = 0

	def connect(self): return True
	def close(self): pass
	def write(self, cmd): self.sent.append(cmd); return True
	def read(self): return True, ""

	def query(self, cmd):
		self.sent.append(cmd)
		self.query_count += 1
		for prefix, reply in self.table.items():
			if cmd.startswith(prefix):
				return True, reply
		return True, "0"

def make_real_osc(table=None):
	""" A RigolDS1000Z in NORMAL mode (dummy=False) backed by canned SCPI responses. """
	return RigolDS1000Z("canned", make_log(), relay=_CannedRelay(table))

def test_driver_return_value_reaches_state():
	""" Drivers now `return` their parsed value; @superreturn captures it into _super_hint for
	the category method to write into state. """

	osc = make_real_osc({":TIM:MAIN:SCAL?": "0.002", ":CHAN1:SCAL?": "0.5"})
	assert osc.get_div_time() == 0.002
	assert osc.state.div_time == 0.002
	assert osc.get_div_volt(1) == 0.5
	assert osc.state.channels[1].div_volt == 0.5

def test_driver_return_value_is_translated_not_raw():
	""" get_coupling maps the instrument's 'AC' onto the category constant. """

	osc = make_real_osc({":CHAN1:COUP?": "AC"})
	assert osc.get_coupling(1) == Oscilloscope.COUPLING_AC
	assert osc.state.channels[1].coupling == Oscilloscope.COUPLING_AC

def test_super_hint_is_cleared_between_calls():
	""" Regression: _super_hint used to persist across calls, so a getter that returns early
	(RigolDS1000Z.get_coupling bails on an unrecognized reply) let the PREVIOUS call's value be
	written into state - silently wrong rather than visibly wrong. """

	osc = make_real_osc({":CHAN1:COUP?": "AC"})
	assert osc.get_coupling(1) == Oscilloscope.COUPLING_AC

	osc.relay.table[":CHAN1:COUP?"] = "NOT-A-COUPLING"
	osc.get_coupling(1)

	assert osc.state.channels[1].coupling is None      # not the stale 'coup-ac'

def test_subclassing_a_driver_does_not_recurse():
	""" Regression: superreturn used `super(type(self), self)`, where type(self) is the RUNTIME
	class. On a subclassed driver that re-found the same wrapper on every hop and recursed until
	the stack blew - and because superreturn wraps the driver body in `except Exception`, the
	RecursionError was SWALLOWED and the call just returned None after ~1000 frames. Fixed by
	capturing the defining class in __set_name__. """

	class MyScope(RigolDS1000Z):
		pass

	osc = MyScope("canned", make_log(), relay=_CannedRelay({":TIM:MAIN:SCAL?": "0.002"}))
	osc.relay.query_count = 0

	assert osc.get_div_time() == 0.002        # would have been None before
	assert osc.relay.query_count == 1         # would have been ~1000 before

def test_subclassed_driver_still_tracks_state():
	class MyScope(RigolDS1000Z):
		pass

	osc = MyScope("canned", make_log(), relay=_CannedRelay({":CHAN2:SCAL?": "0.1"}))
	assert osc.get_div_volt(2) == 0.1
	assert osc.state.channels[2].div_volt == 0.1

def test_superreturn_still_emits_scpi_in_normal_mode():
	""" The dummy-mode work must not have disabled real SCPI output. """

	osc = make_real_osc()
	osc.relay.sent.clear()
	osc.set_div_time(1e-3)
	assert any("TIM:MAIN:SCAL" in c for c in osc.relay.sent)

def test_superreturn_preserves_method_metadata():
	""" The descriptor must still look like the function it wraps, for introspection/docs. """

	assert RigolDS1000Z.get_div_time.__name__ == "get_div_time"
	assert callable(RigolDS1000Z.get_div_time.__get__(None, RigolDS1000Z))

def test_no_driver_assigns_super_hint_directly():
	""" Drivers communicate by returning; only superreturn writes _super_hint. Guard rail. """

	import pathlib, re
	root = pathlib.Path(__file__).resolve().parent.parent / "src" / "constellation"
	offenders = []
	for f in root.rglob("*_dvr.py"):
		for i, line in enumerate(f.read_text().splitlines(), 1):
			if re.match(r'\s*self\._super_hint\s*=', line):
				offenders.append(f"{f.name}:{i}")
	assert offenders == [], f"drivers must `return` their value, not assign _super_hint: {offenders}"

# ---------------------------------------------------------------------------
# InstrumentState path resolution (_resolve, shared by set() and get())
# ---------------------------------------------------------------------------

def _state():
	return make_dummy_osc().state

# --- happy paths ---------------------------------------------------------------------

def test_resolve_scalar_roundtrip():
	st = _state()
	assert st.set(["div_time"], 0.5) is True
	assert st.get(["div_time"]) == 0.5

def test_resolve_indexed_roundtrip():
	st = _state()
	assert st.set(["channels", "div_volt"], 0.25, indices=[2]) is True
	assert st.get(["channels", "div_volt"], indices=[2]) == 0.25
	# other channels untouched
	assert st.get(["channels", "div_volt"], indices=[3]) != 0.25

def test_resolve_fragment_roundtrip():
	st = _state()
	assert st.set(["show_stat_table"], True, fragment="measurements") is True
	assert st.get(["show_stat_table"], fragment="measurements") is True

def test_resolve_whole_indexedlist_slot():
	""" A path ending ON an IndexedList addresses the slot itself, not an attribute of it. """
	st = _state()
	chan = st.get(["channels"], indices=[1])
	assert isinstance(chan, OscilloscopeChannelState)

	replacement = OscilloscopeChannelState(log=make_log())
	replacement.div_volt = 9.9
	assert st.set(["channels"], replacement, indices=[1]) is True
	assert st.get(["channels", "div_volt"], indices=[1]) == 9.9

# --- error paths: every one returns cleanly, none raise ---------------------------------

@pytest.mark.parametrize("desc,params,indices,fragment", [
	("empty params",               [],                        None,   None),
	("unknown top-level param",    ["nope"],                  None,   None),
	("unknown nested param",       ["channels", "bogus"],     [1],    None),
	("IndexedList with no index",  ["channels", "div_volt"],  None,   None),
	("indices too short",          ["channels", "div_volt"],  [],     None),
	("index is None",              ["channels", "div_volt"],  [None], None),
	("index out of range",         ["channels", "div_volt"],  [99],   None),
	("unknown fragment",           ["show_stat_table"],       None,   "nope"),
])
def test_resolve_bad_paths_do_not_raise(desc, params, indices, fragment):
	""" set() must return False and get() must return None for every invalid path - never raise.
	Before _resolve() unified them, an empty params tuple and an unrecognized fragment both
	raised UnboundLocalError, and an out-of-range index raised a bare KeyError. """
	st = _state()
	assert st.set(params, 1, indices=indices, fragment=fragment) is False
	assert st.get(params, indices=indices, fragment=fragment) is None

def test_resolve_bad_path_leaves_state_untouched():
	st = _state()
	st.set(["channels", "div_volt"], 0.5, indices=[1])
	assert st.set(["channels", "div_volt"], 7.7, indices=[99]) is False
	assert st.get(["channels", "div_volt"], indices=[1]) == 0.5

def test_resolve_unpopulated_indexedlist_element_is_reported():
	""" Descending THROUGH an unpopulated slot is an error, not a crash. """
	st = _state()
	st.channels.clear()
	assert st.set(["channels", "div_volt"], 1.0, indices=[1]) is False
	assert st.get(["channels", "div_volt"], indices=[1]) is None

def test_set_and_get_agree_on_what_is_a_valid_path():
	""" The whole point of sharing _resolve(): set() and get() cannot disagree about which
	paths are legal. """
	st = _state()
	paths = [
		(["div_time"], None, None),
		(["channels", "div_volt"], [2], None),
		(["show_stat_table"], None, "measurements"),
		(["nope"], None, None),
		(["channels", "div_volt"], [99], None),
		([], None, None),
		(["x"], None, "nope"),
	]
	for params, indices, fragment in paths:
		set_ok = st.set(params, 1, indices=indices, fragment=fragment)
		get_val = st.get(params, indices=indices, fragment=fragment)
		# set succeeded <=> get resolved the path
		assert set_ok == (get_val is not None), f"disagreement on {params} / {indices} / {fragment}"

def test_set_rejects_a_value_the_indexedlist_type_check_refuses():
	""" IndexedList.validate_type raises TypeError; set() reports it rather than propagating. """
	st = _state()
	assert st.set(["channels"], "not a channel state", indices=[1]) is False
	assert isinstance(st.get(["channels"], indices=[1]), OscilloscopeChannelState)

# ---------------------------------------------------------------------------
# Wrong-name calls / signature mismatches (P9)
# ---------------------------------------------------------------------------

def test_power_supply_refresh_and_apply_do_not_raise():
	""" refresh_data() called get_output_measurement (no such method); apply_state() called
	set_enable_output (no such method, swallowed by a blanket try/except so output enable was
	never restored on any channel). """
	psu = RigolDP832("dummy", make_log(), dummy=True)
	psu.refresh_state()
	psu.refresh_data()
	psu.apply_state()

def test_power_supply_setters_persist_the_right_field():
	psu = RigolDP832("dummy", make_log(), dummy=True)
	psu.set_current(2, 0.33)
	psu.set_output_enable(2, True)
	assert psu.state.channels[2].current_set == 0.33
	assert psu.state.channels[2].enable is True

def test_power_supply_setters_read_back_the_right_parameter():
	""" set_current and set_output_enable both passed `lambda: self.get_voltage(channel)` as
	their readback, so on real hardware they queried VOLTage and left current_set/enable
	unwritten while rewriting voltage_set. """
	psu = RigolDP832("canned", make_log(), relay=_CannedRelay({
		"*IDN?": "RIGOL TECHNOLOGIES,DP832", ":SOUR2:CURR?": "0.33", ":OUTP? CH2": "ON"}))

	psu.relay.sent.clear()
	psu.set_current(2, 0.33)
	assert any("CURR?" in c for c in psu.relay.sent)
	assert not any("VOLT?" in c for c in psu.relay.sent)

	psu.relay.sent.clear()
	psu.set_output_enable(2, True)
	assert any("OUTP?" in c for c in psu.relay.sent)
	assert not any("VOLT?" in c for c in psu.relay.sent)

def test_spectrum_analyzer_refresh_and_apply_do_not_raise():
	""" All three raised AttributeError: refresh_state/apply_state called get_num_points /
	set_num_points (commented out in the same file), and refresh_state/refresh_data called
	get_trace_data(self, idx) - passing self positionally, to a category method that was also
	commented out. """
	sa = SiglentSSA3000X("dummy", make_log(), dummy=True)
	sa.refresh_state()
	sa.refresh_data()
	sa.apply_state()

def test_spectrum_analyzer_category_defines_get_trace_data():
	""" The driver implements get_trace_data under @superreturn, so the category must define it
	or the super lookup raises "'super' object has no attribute 'get_trace_data'". """
	from constellation.all import SpectrumAnalyzer
	assert hasattr(SpectrumAnalyzer, "get_trace_data")

@pytest.mark.parametrize("kwargs,expected", [
	({"channel": 2},     "CHAN2"),
	({"external": True}, "EXT"),
	({"line": True},     "AC"),
])
def test_oscilloscope_trigger_source_survives_apply_state(kwargs, expected):
	""" apply_state() fed the stored string ("CHAN2") into set_trigger_source(channel=...),
	which expects an int -> _format_trigger_source did "CHAN2" < 1 -> TypeError. Restoring a
	saved scope state always failed here. """
	osc = make_dummy_osc()
	osc.set_trigger_source(**kwargs)
	assert osc.state.trigger_source == expected

	osc.apply_state()
	assert osc.state.trigger_source == expected

def test_parse_trigger_source_is_the_inverse_of_format():
	osc = make_dummy_osc()
	for kwargs in ({"channel": 3}, {"external": True}, {"line": True}):
		formatted = osc._format_trigger_source(**kwargs)
		assert osc._parse_trigger_source(formatted) == kwargs

def test_parse_trigger_source_rejects_junk():
	osc = make_dummy_osc()
	assert osc._parse_trigger_source(None) is None
	assert osc._parse_trigger_source("NOT-A-SOURCE") is None
	assert osc._parse_trigger_source("CHANx") is None

def test_batch_hint_only_sent_to_drivers_that_accept_it():
	""" get_all_waveforms() forced _skip_run_management=True onto every driver. RigolDS1000E's
	get_waveform(self, channel) takes no kwargs, so it raised TypeError - swallowed by
	superreturn - turning every waveform into None. """
	osc = make_dummy_osc()
	assert osc._get_waveform_accepts_batch_hint() is True

	class NoKwargsScope(RigolDS1000Z):
		def get_waveform(self, channel):     # mirrors RigolDS1000E's signature
			return {"volt_V": [], "time_s": []}

	narrow = NoKwargsScope("dummy", make_log(), dummy=True)
	assert narrow._get_waveform_accepts_batch_hint() is False

def test_get_all_waveforms_works_for_a_driver_without_kwargs():
	class NoKwargsScope(RigolDS1000Z):
		def get_waveform(self, channel):
			return {"volt_V": [1.0], "time_s": [0.0], "channel": channel}

	osc = NoKwargsScope("dummy", make_log(), dummy=True)
	waves = osc.get_all_waveforms()
	assert len(waves) > 0
	assert all(w is not None for w in waves)

# ---------------------------------------------------------------------------
# query_binary across the network relay boundary
# ---------------------------------------------------------------------------

import base64 as _base64
import struct as _struct
import types as _types
from constellation.relay import RemoteTextCommandRelayClient, RemoteTextCommandRelayListener

class _BinaryCapableRelay(CommandRelay):
	""" Local relay standing in for pyvisa, returning a known list of values. """

	def __init__(self, values):
		super().__init__()
		self.values = values

	def connect(self): return True
	def close(self): pass
	def write(self, cmd): return True
	def read(self): return True, ""
	def query(self, cmd): return True, "x"
	def query_binary(self, cmd, datatype='B'): return True, list(self.values)

class _NoBinaryRelay(_BinaryCapableRelay):
	""" Mimics VICPDirectSCPIRelay, which has no binary support. """
	def query_binary(self, cmd, datatype='B'):
		raise NotImplementedError("this relay does not support query_binary()")

class _LoopbackBinaryClient(RemoteTextCommandRelayClient):
	""" A RemoteTextCommandRelayClient whose RPC goes straight into a listener instead of over
	the broker, so the encode/decode contract can be exercised with no live mesh. """

	def __init__(self, listener):
		super().__init__()
		self.listener = listener
		# stand in for the labmesh RelayClient
		self.relay_client = _types.SimpleNamespace(
			call=lambda name, kw: getattr(self.listener, name)(**kw))

	def _run(self, coro, timeout_s=None):
		return coro          # the "coroutine" is already the listener's return value

def _binary_pair(values):
	log = make_log()
	listener = RemoteTextCommandRelayListener("addr", log, local_relay=_BinaryCapableRelay(values))
	client = _LoopbackBinaryClient(listener)
	client.configure("relay-1", log)
	return client

@pytest.mark.parametrize("datatype,values", [
	("B", [0, 1, 127, 255] * 3),                 # unsigned bytes - the Rigol's format
	("h", [-32768, -1, 0, 1, 32767]),            # signed 16-bit, exercises sign handling
	("i", [-(2**31), 0, 2**31 - 1]),             # signed 32-bit
	("B", []),                                   # empty block
], ids=["byte", "int16", "int32", "empty"])
def test_query_binary_roundtrips_exactly(datatype, values):
	""" Regression: RemoteTextCommandRelayClient had no query_binary at all, so the base class
	raised NotImplementedError, Driver.query_binary swallowed it, and every waveform captured
	over labmesh came back as [] - silently, since binary=True is get_waveform()'s default. """
	client = _binary_pair(values)
	ok, got = client.query_binary(":WAV:DATA?", datatype=datatype)
	assert ok is True
	assert got == values

def test_query_binary_roundtrips_floats():
	client = _binary_pair([1.5, -2.25, 0.0, 1024.0])
	ok, got = client.query_binary(":WAV:DATA?", datatype="f")
	assert ok is True
	assert got == pytest.approx([1.5, -2.25, 0.0, 1024.0])

def test_query_binary_roundtrips_a_full_size_waveform():
	""" 250k points is the DS1000Z's max single-chunk transfer, i.e. the realistic worst case. """
	values = [i % 256 for i in range(250000)]
	client = _binary_pair(values)
	ok, got = client.query_binary(":WAV:DATA?", datatype="B")
	assert ok is True
	assert got == values

def test_query_binary_is_endian_explicit():
	""" The payload must be little-endian regardless of host byte order, so a bench machine and
	a controlling machine of different architectures interoperate. """
	values = [1, 256, 65535]
	listener = RemoteTextCommandRelayListener("addr", make_log(),
	                                          local_relay=_BinaryCapableRelay(values))
	ok, payload = listener.query_binary(":WAV:DATA?", datatype="H")
	assert ok is True
	assert _base64.b64decode(payload) == _struct.pack("<3H", *values)

def test_query_binary_fails_cleanly_when_local_relay_cannot():
	""" A VICP relay has no binary support; that must surface as a clean failure rather than an
	unhandled NotImplementedError inside the RelayAgent. """
	listener = RemoteTextCommandRelayListener("addr", make_log(), local_relay=_NoBinaryRelay([]))
	assert listener.query_binary(":WAV:DATA?") == [False, ""]

def test_query_binary_client_reports_failure_without_raising():
	log = make_log()
	listener = RemoteTextCommandRelayListener("addr", log, local_relay=_NoBinaryRelay([]))
	client = _LoopbackBinaryClient(listener)
	client.configure("relay-1", log)
	assert client.query_binary(":WAV:DATA?") == (False, [])

def test_query_binary_client_rejects_a_corrupt_payload():
	""" A truncated block must be reported, not silently decoded into garbage. """
	log = make_log()
	listener = RemoteTextCommandRelayListener("addr", log,
	                                          local_relay=_BinaryCapableRelay([1, 2, 3]))
	client = _LoopbackBinaryClient(listener)
	client.configure("relay-1", log)
	# 3 bytes is not a whole number of int32s
	client.relay_client = _types.SimpleNamespace(
		call=lambda name, kw: [True, _base64.b64encode(b"\x01\x02\x03").decode()])
	assert client.query_binary(":WAV:DATA?", datatype="i") == (False, [])

def test_query_binary_uses_a_longer_timeout_than_text():
	""" A full-memory :WAV:DATA? chunk can legitimately take 15-20+ s (DirectSCPIRelay allows
	30 s for it), so the 10 s text timeout would abandon the transfer mid-flight. """
	client = RemoteTextCommandRelayClient()
	assert client.binary_timeout_s > client.timeout_s
	assert client.binary_timeout_s >= 30.0

# ---------------------------------------------------------------------------
# write_binary: pushing bulk data TO an instrument (e.g. an AWG waveform)
# ---------------------------------------------------------------------------

class _BinaryWriteCapture(CommandRelay):
	""" Records what write_binary() hands to the local relay. """

	def __init__(self):
		super().__init__()
		self.received = None

	def connect(self): return True
	def close(self): pass
	def write(self, cmd): return True
	def read(self): return True, ""
	def query(self, cmd): return True, ""

	def write_binary(self, cmd, values, datatype='B'):
		self.received = (cmd, list(values), datatype)
		return True

class _FakeVisaInst:
	""" Stands in for a pyvisa Resource for write_binary_values(). """

	def __init__(self):
		self.calls = []

	def write_binary_values(self, cmd, values, datatype='B', is_big_endian=False):
		self.calls.append({"cmd": cmd, "values": list(values),
		                   "datatype": datatype, "is_big_endian": is_big_endian})

def test_direct_relay_write_binary_forwards_to_pyvisa():
	relay = DirectSCPIRelay()
	relay.configure("addr", make_log())
	relay.inst = _FakeVisaInst()

	assert relay.write_binary(":WVDT ", [1, 2, 250], datatype="B") is True
	call = relay.inst.calls[0]
	assert call["values"] == [1, 2, 250]
	assert call["datatype"] == "B"
	# must match the little-endian convention the network relay uses, so a block behaves the
	# same whether it was sent locally or over the mesh
	assert call["is_big_endian"] is False

def test_direct_relay_write_binary_reports_failure():
	class Boom(_FakeVisaInst):
		def write_binary_values(self, *a, **k): raise RuntimeError("instrument said no")
	relay = DirectSCPIRelay()
	relay.configure("addr", make_log())
	relay.inst = Boom()
	assert relay.write_binary(":WVDT ", [1], datatype="B") is False

def test_vicp_relay_builds_an_ieee_definite_length_header():
	""" pyvicp has no write_binary_values(), so the #<ndigits><count> header is built by hand. """
	relay = VICPDirectSCPIRelay()
	relay.configure("addr", make_log())

	class FakeVicp:
		def __init__(self): self.sent = b""
		def send(self, b): self.sent += b
	relay.inst = FakeVicp()

	assert relay.write_binary("C1:WF ", [1, 2, 3], datatype="B") is True
	# 3 payload bytes -> count "3" is 1 digit -> "#13"
	assert relay.inst.sent == b"C1:WF #13\x01\x02\x03"

def test_vicp_header_digit_count_scales():
	relay = VICPDirectSCPIRelay()
	relay.configure("addr", make_log())
	class FakeVicp:
		def __init__(self): self.sent = b""
		def send(self, b): self.sent += b
	relay.inst = FakeVicp()

	relay.write_binary("C1:WF ", [0] * 1234, datatype="B")
	# 1234 bytes -> count "1234" is 4 digits -> "#41234"
	assert relay.inst.sent.startswith(b"C1:WF #41234")

def _write_binary_pair():
	log = make_log()
	capture = _BinaryWriteCapture()
	listener = RemoteTextCommandRelayListener("addr", log, local_relay=capture)
	client = _LoopbackBinaryClient(listener)
	client.configure("relay-1", log)
	return client, capture

@pytest.mark.parametrize("datatype,values", [
	("B", [0, 1, 127, 255]),
	("h", [-32768, -1, 0, 32767]),
	("i", [-(2**31), 0, 2**31 - 1]),
	("B", []),
], ids=["byte", "int16", "int32", "empty"])
def test_write_binary_crosses_the_network_exactly(datatype, values):
	client, capture = _write_binary_pair()
	assert client.write_binary(":ARB:DATA ", values, datatype=datatype) is True
	cmd, got, dt = capture.received
	assert cmd == ":ARB:DATA "
	assert got == values
	assert dt == datatype

def test_write_binary_crosses_the_network_for_a_full_waveform():
	client, capture = _write_binary_pair()
	values = [i % 256 for i in range(250000)]
	assert client.write_binary(":ARB:DATA ", values, datatype="B") is True
	assert capture.received[1] == values

def test_write_binary_fails_cleanly_when_local_relay_cannot():
	""" A relay with no binary-write support must surface as False, not an unhandled
	NotImplementedError inside the RelayAgent. """
	class NoBinaryWrite(_BinaryWriteCapture):
		def write_binary(self, cmd, values, datatype='B'):
			raise NotImplementedError("no binary write here")
	listener = RemoteTextCommandRelayListener("addr", make_log(), local_relay=NoBinaryWrite())
	payload = _base64.b64encode(_struct.pack("<3B", 1, 2, 3)).decode()
	assert listener.write_binary(":ARB:DATA ", payload, datatype="B") is False

def test_write_binary_listener_rejects_a_corrupt_payload():
	listener = RemoteTextCommandRelayListener("addr", make_log(), local_relay=_BinaryWriteCapture())
	# 3 bytes is not a whole number of int32s
	payload = _base64.b64encode(b"\x01\x02\x03").decode()
	assert listener.write_binary(":ARB:DATA ", payload, datatype="i") is False

def test_write_binary_client_reports_failure_without_raising():
	client, _ = _write_binary_pair()
	client.relay_client = None
	assert client.write_binary(":ARB:DATA ", [1, 2, 3]) is False

def test_driver_write_binary_is_a_noop_in_dummy_mode():
	""" Dummy mode must not touch the relay, but must still report success so callers that
	check the result behave the same as they would against hardware. """
	osc = make_dummy_osc()
	assert osc.write_binary(":ARB:DATA ", [1, 2, 3]) is True

def test_driver_write_binary_forwards_to_the_relay():
	osc = make_real_osc()
	captured = {}
	osc.relay.write_binary = lambda cmd, values, datatype='B': captured.update(
		cmd=cmd, values=list(values), datatype=datatype) or True

	assert osc.write_binary(":ARB:DATA ", [1, 2, 3], datatype="B") is True
	assert captured["values"] == [1, 2, 3]

def test_driver_write_binary_refuses_when_offline():
	osc = make_real_osc()
	osc.online = False
	assert osc.write_binary(":ARB:DATA ", [1, 2, 3]) is False

# ---------------------------------------------------------------------------
# IndexedList cleanup (P6)
# ---------------------------------------------------------------------------

from constellation.base import ChannelList
from stardust.serializer import to_serial_dict, from_serial_dict

def test_validate_type_survives_serialization():
	""" Regression: validate_type was deliberately excluded from __state_fields__, and stardust
	rebuilds via cls.__new__(cls) without calling __init__ - so the attribute simply didn't exist
	on a restored object and EVERY write raised AttributeError. """
	il = IndexedList(1, 4, validate_type=OscilloscopeChannelState)
	il[1] = OscilloscopeChannelState(log=make_log())

	restored = from_serial_dict(to_serial_dict(il))

	assert restored.validate_type is OscilloscopeChannelState
	restored[2] = OscilloscopeChannelState(log=make_log())     # must not raise
	with pytest.raises(TypeError):
		restored[3] = 12345

def test_indexed_list_without_validate_type_survives_serialization():
	il = IndexedList(1, 3)
	il[1] = 5
	restored = from_serial_dict(to_serial_dict(il))
	assert restored.validate_type is None
	restored[2] = "anything"          # no type checking configured, so anything goes
	assert restored[2] == "anything"

def test_append_works_after_a_state_restore(tmp_path):
	""" The concrete consequence of the bug above: MeasurementsMixin.add_measurement uses
	IndexedList.append(), so adding a measurement after restore_state() blew up. """
	osc = make_dummy_osc()
	fn = str(tmp_path / "state.hdf")
	osc.dump_state(fn)
	osc.restore_state(fn)

	assert osc.add_measurement(1, MeasurementsMixin.MEAS_VPP) is True

def test_summarize_handles_non_instrumentstate_values():
	""" Regression: summarize() called .state_str() unconditionally, so a list of plain values
	raised AttributeError. validate_type is optional, so nothing prevents such a list. """
	il = IndexedList(1, 3)
	il[1] = 3.14
	il[2] = "text"
	out = il.summarize()
	assert "3.14" in out
	assert "text" in out

def test_summarize_still_formats_instrumentstate_values():
	il = IndexedList(1, 2, validate_type=OscilloscopeChannelState)
	il[1] = OscilloscopeChannelState(log=make_log())
	assert "div_volt" in il.summarize()

def test_summarize_empty_list():
	assert "Empty" in IndexedList(1, 3).summarize()

def test_append_allow_expand_grows_a_full_list():
	""" Regression: allow_expand was accepted and ignored (a TODO in the body), so a full list
	returned False even when the caller explicitly permitted growth. """
	il = IndexedList(0, 2)
	assert il.append("a") is True
	assert il.append("b") is True

	assert il.append("c") is False                      # default: refuse to grow
	assert il.num_indices == 2

	assert il.append("c", allow_expand=True) is True    # explicit: grow by one
	assert il.num_indices == 3
	assert il[2] == "c"
	assert list(il.populated_items()) == [(0, "a"), (1, "b"), (2, "c")]

def test_append_allow_expand_type_checks_before_growing():
	""" A rejected value must not leave the list permanently one slot larger and empty. """
	il = IndexedList(0, 1, validate_type=str)
	il.append("x")
	with pytest.raises(TypeError):
		il.append(999, allow_expand=True)
	assert il.num_indices == 1

def test_get_and_set_idx_val_are_aliases_not_copies():
	""" The pairs used to be independent implementations of the same logic. They must now agree
	on every case, including out-of-range and unpopulated. """
	il = IndexedList(1, 4, validate_type=str)
	il.set_idx_val(2, "b")

	assert il[2] == il.get_idx_val(2) == "b"
	assert il[3] is il.get_idx_val(3) is None

	for call in (lambda: il[99], lambda: il.get_idx_val(99)):
		with pytest.raises(KeyError):
			call()
	for call in (lambda: il.__setitem__(99, "x"), lambda: il.set_idx_val(99, "x")):
		with pytest.raises(KeyError):
			call()
	for call in (lambda: il.__setitem__(1, 5), lambda: il.set_idx_val(1, 5)):
		with pytest.raises(TypeError):
			call()

def test_channellist_is_an_alias_not_a_subclass():
	""" It must be the same class object - a subclass would register a second name in stardust's
	registry and break deserialization of anything already stored as an "IndexedList". """
	assert ChannelList is IndexedList

# ---------------------------------------------------------------------------------------------
# Partial category compliance: @feature_unavailable (todo P2)
#
# Not every instrument can implement every method of its category. RigolDS1000E is the reference
# case: its SCPI interface has no timebase control at all, so before this mechanism existed the
# class was missing abstract methods and could not be constructed - one hardware gap made the
# entire driver unusable.
# ---------------------------------------------------------------------------------------------

from constellation.base import FeatureUnavailable, feature_unavailable
from constellation.instrument_control.oscilloscope.drivers.Rigol_DS1000E_dvr import RigolDS1000E

def make_dummy_ds1000e():
	return RigolDS1000E("DUMMY", make_log(), relay=DirectSCPIRelay(), dummy=True)

def test_partially_compliant_driver_is_constructible():
	""" The whole point: a driver with genuine hardware gaps must still be instantiable, so the
	majority of it that does work is reachable. """
	d = make_dummy_ds1000e()
	assert isinstance(d, Oscilloscope)
	assert RigolDS1000E.__abstractmethods__ == frozenset()

def test_unavailable_feature_raises_when_called_directly():
	d = make_dummy_ds1000e()
	with pytest.raises(FeatureUnavailable) as excinfo:
		d.get_div_time()
	msg = str(excinfo.value)
	# The message must name both the method and the hardware reason - "unsupported" alone sends
	# the reader back to the source to find out why.
	assert "get_div_time" in msg
	assert "timebase" in msg

def test_unavailable_feature_raises_in_dummy_mode_too():
	""" Dummy mode simulates THIS instrument, and this instrument cannot do it. A dummy that
	quietly succeeded would hide the failure until hardware day. """
	d = make_dummy_ds1000e()
	assert d.dummy
	with pytest.raises(FeatureUnavailable):
		d.set_div_time(1e-3)

def test_unavailable_feature_does_not_write_state():
	""" The old implementation warned and then fell through to the category method, which wrote
	the requested value into self.state - so the tracker claimed a timebase the instrument had
	never been told about. """
	d = make_dummy_ds1000e()
	with pytest.raises(FeatureUnavailable):
		d.set_div_time(42e-3)
	assert d.state.get(["div_time"]) != 42e-3

def test_capability_is_introspectable_before_calling():
	""" A GUI wants to grey out a control, not catch an exception after the user clicks it. """
	d = make_dummy_ds1000e()

	assert d.feature_is_available("get_div_volt") is True
	assert d.feature_is_available("get_div_time") is False
	# A name the driver doesn't have at all also can't be called.
	assert d.feature_is_available("no_such_method") is False

	unavailable = d.unavailable_features()
	assert "get_div_time" in unavailable
	assert "get_div_volt" not in unavailable
	assert "timebase" in unavailable["get_div_time"]

def test_fully_compliant_driver_reports_no_unavailable_features():
	d = make_dummy_osc()
	assert d.unavailable_features() == {}
	assert d.feature_is_available("get_div_time") is True

def test_state_sweeps_skip_unavailable_features_instead_of_aborting():
	""" refresh_state/apply_state call every getter/setter unconditionally, so one
	FeatureUnavailable would abort the sweep partway through and leave the rest of the state
	stale. Inside a sweep they're skipped. """
	d = make_dummy_ds1000e()

	d.refresh_state()
	d.apply_state()
	d.refresh_data()

	# The supported parameters were still swept despite the unavailable ones in the same loop.
	assert d.state.get(["channels", "div_volt"], indices=[1, None]) is not None

def test_sweep_suppression_is_scoped_to_the_sweep():
	""" Suppression must not leak: a direct call after a sweep still raises. """
	d = make_dummy_ds1000e()
	d.refresh_state()
	assert d._state_sweep_depth == 0
	with pytest.raises(FeatureUnavailable):
		d.get_div_time()

def test_sweep_depth_unwinds_when_the_sweep_raises():
	""" The depth counter is decremented in a finally block, so an unrelated failure mid-sweep
	can't leave the driver permanently suppressing FeatureUnavailable. """
	d = make_dummy_ds1000e()

	def boom():
		raise ValueError("unrelated failure")

	d.refresh_state = d._as_state_sweep(boom)
	with pytest.raises(ValueError):
		d.refresh_state()

	assert d._state_sweep_depth == 0
	with pytest.raises(FeatureUnavailable):
		d.get_div_time()

def test_dummy_waveforms_survive_a_missing_timebase():
	""" init_dummy_state() seeds defaults through the setters, so on a scope with no timebase
	div_time stays None. remake_dummy_waves() must fall back rather than raising TypeError. """
	d = make_dummy_ds1000e()
	assert d.state.get(["div_time"]) is None

	wave = d.state.get(["channels", "waveform"], indices=[1, None])
	assert len(wave["volt_V"]) > 0
	assert len(wave["time_s"]) == len(wave["volt_V"])

def test_feature_unavailable_marker_is_readable_on_the_class():
	""" Introspection must work without instantiating - unavailable_features() reads the class,
	and tooling may want the same before a driver is connected. """
	reason = getattr(RigolDS1000E.get_div_time, "__feature_unavailable__", None)
	assert reason is not None
	assert "timebase" in reason

def test_feature_unavailable_preserves_the_method_signature():
	""" functools.wraps keeps the name and docstring, so introspection and help() still work. """
	assert RigolDS1000E.set_div_volt.__name__ == "set_div_volt"
	assert RigolDS1000E.get_div_time.__name__ == "get_div_time"

# ---------------------------------------------------------------------------------------------
# Automatic state validation: InstrumentState.__init_subclass__ (todo P7)
#
# A state class has to keep two lists in sync - stardust's class-level __state_fields__
# serialization manifest, and the per-instance units/is_data registry built by add_param().
# validate() catches drift between them, but only if it's called, and three state classes never
# called it (one had real drift as a result). The hook wraps every subclass's __init__ so
# validation happens automatically.
# ---------------------------------------------------------------------------------------------

def capture_warnings(log):
	""" Returns a list that collects every warning message logged to `log`. """
	captured = []
	original = log.warning
	def spy(message, detail=""):
		captured.append(message)
		return original(message, detail)
	log.warning = spy
	return captured

def test_auto_validate_catches_a_field_missing_from_state_fields():
	""" The drift that actually matters: registered with add_param() but absent from the
	manifest, so the field silently does not serialize. """
	log = make_log()
	warnings = capture_warnings(log)

	class DriftyState(InstrumentState):
		__state_fields__ = ("declared",)
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("declared", unit="V")
			self.add_param("forgotten", unit="V")

	DriftyState(log=log)

	assert len(warnings) == 1
	assert "forgotten" in warnings[0]
	assert "__state_fields__" in warnings[0]

def test_auto_validate_catches_a_field_missing_from_add_param():
	log = make_log()
	warnings = capture_warnings(log)

	class OtherDriftState(InstrumentState):
		__state_fields__ = ("declared", "never_registered")
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("declared", unit="V")

	OtherDriftState(log=log)

	assert len(warnings) == 1
	assert "never_registered" in warnings[0]
	assert "add_param" in warnings[0]

def test_auto_validate_is_silent_when_the_lists_agree():
	log = make_log()
	warnings = capture_warnings(log)

	class CleanState(InstrumentState):
		__state_fields__ = ("voltage",)
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("voltage", unit="V")

	assert CleanState(log=log).validate() is True
	assert warnings == []

def test_auto_validate_fires_exactly_once_per_construction():
	""" The `type(self) is cls` guard: without it a two-level hierarchy validates twice, once
	per level, doubling every warning. """
	log = make_log()

	class BaseState(InstrumentState):
		__state_fields__ = ("a",)
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("a", unit="")
			self.add_param("undeclared", unit="")

	class DerivedState(BaseState):
		__state_fields__ = ("b",)
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("b", unit="")

	warnings = capture_warnings(log)
	DerivedState(log=log)
	assert len(warnings) == 1

def test_auto_validate_fires_for_a_subclass_that_inherits_init():
	""" This is why the wrapper must NOT skip an already-wrapped __init__: a subclass that
	defines no __init__ of its own would then never validate. """
	log = make_log()

	class ParentState(InstrumentState):
		__state_fields__ = ("a",)
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("a", unit="")
			self.add_param("undeclared", unit="")

	class InheritsInitState(ParentState):
		__state_fields__ = ()

	warnings = capture_warnings(log)
	InheritsInitState(log=log)
	assert len(warnings) == 1

def test_auto_validate_cooperates_with_serializable_registration():
	""" Serializable uses __init_subclass__ too, for class registration and the parent
	__state_fields__ merge. The hook must call super().__init_subclass__ first or both break. """
	from stardust.serializer import SERIALIZABLE_CLASS_REGISTRY

	class RegisteredState(InstrumentState):
		__state_fields__ = ("thing",)
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("thing", unit="")

	assert "RegisteredState" in SERIALIZABLE_CLASS_REGISTRY
	# The parent-field merge must still have happened.
	assert "units" in RegisteredState.__state_fields__

def test_validate_does_not_print_to_stdout(capsys):
	""" It used to print() with colorama in addition to logging - wrong for a library, and far
	worse now that it runs on every state object ever constructed. """
	log = make_log()

	class NoisyState(InstrumentState):
		__state_fields__ = ()
		def __init__(self, log=None):
			super().__init__(log=log)
			self.add_param("unmanifested", unit="")

	NoisyState(log=log)
	assert capsys.readouterr().out == ""

def test_measurement_setting_serializes_its_value():
	""" OscilloscopeMeasurementSetting registered last_measured_value with add_param() but left
	it out of __state_fields__, so a saved measurement came back without its value. Found by the
	auto-validate hook. """
	from constellation.instrument_control.oscilloscope.oscilloscope_ctg import OscilloscopeMeasurementSetting

	assert "last_measured_value" in OscilloscopeMeasurementSetting.__state_fields__

	log = make_log()
	warnings = capture_warnings(log)
	OscilloscopeMeasurementSetting(log=log)
	assert warnings == []

def test_working_drivers_validate_cleanly():
	""" Turning auto-validation on must not be a noise event - if it warned on ordinary drivers
	nobody would read the warnings. """
	log = make_log()
	warnings = capture_warnings(log)

	# Pass the spied log explicitly - make_dummy_osc() builds its own, which the spy can't see.
	RigolDS1000Z("DUMMY", log=log, relay=DirectSCPIRelay(), dummy=True)
	RigolDP832("DUMMY", log, relay=DirectSCPIRelay(), dummy=True)
	SiglentSSA3000X("DUMMY", log, relay=DirectSCPIRelay(), dummy=True)

	assert warnings == []
