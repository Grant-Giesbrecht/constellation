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
from constellation.relay import DirectSCPIRelay
from constellation.instrument_control.oscilloscope.oscilloscope_ctg import Oscilloscope
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

@pytest.mark.xfail(strict=True, reason=(
	"BUG: InstrumentState.set() references the local variable `obj_top` in its error message "
	"for an unrecognized `fragment` name before `obj_top` is ever assigned (that assignment only "
	"happens later, in the non-fragment code path). This raises UnboundLocalError instead of "
	"logging an error and returning False like every other invalid-input path in set()/get()."
))
def test_instrumentstate_set_bad_fragment_fails_gracefully():
	s = _DemoState(log=make_log())
	result = s.set(["volt"], 1.0, fragment="no_such_fragment")
	assert result is False

@pytest.mark.xfail(strict=True, reason=(
	"BUG: InstrumentState.get() has no `fragment` parameter at all, unlike set() - there is no "
	"way to read back a value that was written into a state_fragment via set(..., fragment=X) "
	"through the top-level state.get() API."
))
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

@pytest.mark.xfail(strict=True, reason=(
	"BUG: set_trigger_mode is decorated with @enabledummy at the category level (unlike "
	"set_div_volt/set_offset_volt/set_chan_enable/set_coupling/set_div_time/set_offset_time, "
	"which are not). In dummy mode this routes the call to dummy_responder() instead of the "
	"normal modify_state() path, and Oscilloscope.dummy_responder() has no case for "
	"'set_trigger_mode' - so it hits the generic fallback, returns -1, and never touches "
	"self.state at all. The state silently stays unset."
))
def test_dummy_set_trigger_mode_persists_to_state():
	osc = make_dummy_osc()
	osc.set_trigger_mode(Oscilloscope.TRIG_AUTO)
	assert osc.state.trigger_mode == Oscilloscope.TRIG_AUTO

@pytest.mark.xfail(strict=True, reason="Same root cause as test_dummy_set_trigger_mode_persists_to_state, for set_trigger_level.")
def test_dummy_set_trigger_level_persists_to_state():
	osc = make_dummy_osc()
	osc.set_trigger_level(1.5)
	assert osc.state.trigger_level == 1.5

@pytest.mark.xfail(strict=True, reason="Same root cause as test_dummy_set_trigger_mode_persists_to_state, for set_probe_attenuation.")
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

@pytest.mark.xfail(strict=True, reason=(
	"BUG: RigolDS1000Z.__init__ (and Rigol_DS1000E, Siglent_SSA3000X) declares "
	"`relay:CommandRelay=DirectSCPIRelay()` as a default parameter value. Python evaluates "
	"default values once, at function-definition time, so every instance constructed without "
	"an explicit relay= kwarg shares the exact same DirectSCPIRelay object - including its "
	"`address`, meaning constructing a second instrument silently clobbers the first "
	"instrument's relay address (confirmed: osc1.relay.address changes after osc2 is built)."
))
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
	"jarnsaxa.dict_to_hdf's write_level() creates an HDF group per dict, then h5py.Group."
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
