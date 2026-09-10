""" Hardware verification for the arbitrary-waveform-generator category.

Run against an instrument on the bench:

	pytest tests/hardware --driver=SiglentSDG2000X --address=TCPIP0::192.168.1.90::INSTR
	pytest tests/hardware --driver=SiglentSDG2000X --address=... --confirm

The first form is round-trip mode: set a parameter, read it back, check they agree. Fast and
unattended, and it earns a `roundtrip` record.

The second is moderated mode. It pauses after each check and asks a human to look at the
instrument, because **a round trip can be self-consistently wrong**. That failure has a specific
shape on a signal generator: every basic-wave parameter is set and read through the same `BSWV`
keyword block, so a driver that writes AMP where it means OFST reads its own mistake back
perfectly. Only someone watching the front panel - or a scope on the output - catches it, which is
why every prompt below names the physical thing to look at and the control it would most plausibly
be confused with. Passing moderated mode earns a `confirmed` record.

WHAT THIS DRIVES INTO THE WORLD. Unlike a scope, an AWG has an output. The checks below keep the
amplitude at or below 2 Vpp and only enable the output in the one check that is about enabling the
output, so a probe left on the bench is not a hazard - but the instrument IS generating during the
run, and the `instrument` fixture restores its entry state at the end rather than leaving the
output on.

Results are written to the driver's verification.yaml at the end of the run. See
docs/hardware_verification.md.
"""

import pytest

from constellation.instrument_control.arb_waveform_generator.arb_waveform_generator_ctg import ArbitraryWaveformGenerator

from hardware_support import Check, close_enough, requires_category, run_check, skip_if_unavailable

pytestmark = pytest.mark.hardware

@pytest.fixture(autouse=True)
def _category(instrument):
	''' A run has one instrument on the bench but collects every category module, so a module that
	is not about that instrument steps aside rather than failing. '''

	requires_category(instrument, ArbitraryWaveformGenerator)

# A quiet, well-defined operating point: a parameter has to exist before it can be verified, and
# on an AWG that depends on the waveform in force - a generator making noise has no frequency and
# no peak-to-peak amplitude to read back. Every check that is not itself about the waveform type
# starts from here.
BASELINE_WAVE = ArbitraryWaveformGenerator.WAVE_SINE
BASELINE_FREQ = 1e3
BASELINE_AMPL = 1.0
BASELINE_OFFS = 0.0

def _baseline(awg, ch) -> None:
	''' Puts the channel on a 1 kHz, 1 Vpp, 0 V sine.

	This uses methods that are themselves under test, which is unavoidable rather than sloppy:
	there is no way to read a frequency back off a generator without first telling it to make
	something that has one. A driver broken badly enough to fail here fails its own check too, and
	the setup steps are skipped rather than failed when the driver declares it can't do them.
	'''

	for name, args in (
		("set_waveform", (ch, BASELINE_WAVE)),
		("set_frequency", (ch, BASELINE_FREQ)),
		("set_amplitude", (ch, BASELINE_AMPL)),
		("set_offset", (ch, BASELINE_OFFS)),
	):
		if awg.feature_is_available(name):
			getattr(awg, name)(*args)

def _wave_name(wave:str) -> str:
	''' The category constant as a word for a prompt ("wave-square" -> "SQUARE"). '''

	return wave.replace("wave-", "").upper()

# Every set/get pair the ArbitraryWaveformGenerator category declares.
CHECKS = [

	Check(
		methods=("set_waveform", "get_waveform"),
		# SINE and SQUARE are the ordinary cases. NOISE and DC are in the list deliberately: they
		# are the replies whose shape differs - a NOISE reply carries STDEV/MEAN and no frequency
		# or amplitude at all, and a DC reply carries almost nothing. A driver that reads the
		# BSWV block by fixed token position (this one used to) round-trips SINE perfectly and
		# misreads every field on these two.
		values=(BASELINE_WAVE, ArbitraryWaveformGenerator.WAVE_NOISE,
			ArbitraryWaveformGenerator.WAVE_DC, ArbitraryWaveformGenerator.WAVE_SQUARE),
		apply=lambda awg, ch, value: awg.set_waveform(ch, value),
		read=lambda awg, ch: awg.get_waveform(ch),
		matches=lambda expected, actual: expected == actual,
		setup=_baseline,
		prompt=lambda awg, ch, value: (
			f"Channel {ch}'s waveform readout should say {_wave_name(value)}, and the preview on the "
			f"display should be a square wave. Check the CHANNEL as well as the shape - a driver that "
			f"ignores its channel argument and always writes channel 1 round-trips perfectly. This "
			f"check also passed through NOISE and DC on its way here; if either of those looked wrong "
			f"on screen, answer no."),
	),

	Check(
		methods=("set_frequency", "get_frequency"),
		values=(BASELINE_FREQ, 1e5),
		apply=lambda awg, ch, value: awg.set_frequency(ch, value),
		read=lambda awg, ch: awg.get_frequency(ch),
		# A generator synthesises its frequency from a clock rather than snapping to a 1-2-5 grid,
		# so the tolerance is far tighter than a scope's. A loose one here would pass a driver
		# that dropped or gained a factor of 1000 on a unit suffix, which is exactly the bug this
		# parameter is prone to ("1.5MHZ" is not 1.5).
		matches=lambda expected, actual: close_enough(expected, actual, rel=1e-4),
		setup=_baseline,
		prompt=lambda awg, ch, value: (
			f"Channel {ch}'s FREQUENCY should read {value/1e3:g} kHz. Check the units on the display: "
			f"a driver that mis-parses the instrument's unit suffix reads back a number that looks "
			f"entirely reasonable and is off by a factor of 1000. If the PERIOD field changed while "
			f"frequency did not, the driver is writing PERI where it should write FRQ."),
	),

	Check(
		methods=("set_amplitude", "get_amplitude"),
		values=(BASELINE_AMPL, 2.0),
		apply=lambda awg, ch, value: awg.set_amplitude(ch, value),
		read=lambda awg, ch: awg.get_amplitude(ch),
		matches=lambda expected, actual: close_enough(expected, actual, rel=0.02, abs_tol=0.01),
		setup=_baseline,
		prompt=lambda awg, ch, value: (
			f"Channel {ch}'s AMPLITUDE should read {value:g} Vpp - the peak-to-peak field, not the "
			f"Vrms or the high/low levels beside it, all of which move together when amplitude "
			f"changes and any of which a driver could be writing. Note that the displayed value "
			f"depends on the channel's output LOAD setting (HiZ vs 50 ohm); this checks what the "
			f"instrument reports for the load it is currently set to."),
	),

	Check(
		methods=("set_offset", "get_offset"),
		values=(BASELINE_OFFS, 0.5),
		apply=lambda awg, ch, value: awg.set_offset(ch, value),
		read=lambda awg, ch: awg.get_offset(ch),
		# 0 V is one of the values, so a purely relative tolerance would compare against zero and
		# only ever match exactly.
		matches=lambda expected, actual: close_enough(expected, actual, rel=0.02, abs_tol=0.01),
		setup=_baseline,
		prompt=lambda awg, ch, value: (
			f"Channel {ch}'s DC OFFSET should read {value:g} V while the amplitude stays at "
			f"{BASELINE_AMPL:g} Vpp. On a scope watching the output, the trace should shift up "
			f"without changing height. If it got taller instead, offset and amplitude are crossed - "
			f"they are adjacent keywords in the same command block, so this is the plausible mix-up."),
	),

	Check(
		methods=("set_output_enable", "get_output_enable"),
		# Ends on True so the operator is asked about the state that has a visible indicator. The
		# baseline has already set a 1 Vpp sine, so what appears at the connector is benign and
		# known; the instrument fixture restores the entry state when the run finishes.
		values=(False, True),
		apply=lambda awg, ch, value: awg.set_output_enable(ch, value),
		read=lambda awg, ch: awg.get_output_enable(ch),
		matches=lambda expected, actual: bool(expected) == bool(actual),
		setup=_baseline,
		prompt=lambda awg, ch, value: (
			f"Channel {ch}'s OUTPUT should now be ON - its output key is lit and a "
			f"{BASELINE_AMPL:g} Vpp {BASELINE_FREQ/1e3:g} kHz sine is present at the connector. Check "
			f"the other channel's output key is untouched: a driver that writes the wrong channel "
			f"number still round-trips if its getter reads back the same wrong channel."),
	),
]

@pytest.mark.parametrize("check", CHECKS, ids=[c.id for c in CHECKS])
def test_roundtrip(check, instrument, channel, recorder, confirm, moderated, request):
	""" Set a parameter, read it back, and (in moderated mode) have a human confirm the instrument
	physically did it. """

	run_check(check, instrument, channel, recorder, confirm, moderated, request.config.getoption("--recheck"))

def test_refresh_state_reads_every_parameter(instrument, channel, request):
	""" refresh_state() is worth its own hardware check on this category because a driver is free
	to override it with a single bulk query - SiglentSDG2000X does, because one `BSWV?` returns
	the whole basic-wave block and four category getters would be four round trips for it. That
	override is a second parse of the instrument's reply, and nothing else exercises it.

	It records nothing. The status of a getter describes that getter's own code, and this test
	never calls one; writing a record from here would attach evidence to a code hash the run
	didn't execute, which is the laundering the staleness layer exists to prevent.
	"""

	skip_if_unavailable(type(instrument), ["set_frequency", "set_amplitude"])

	# Dummy mode cannot reach this test's point. It works by blanking the tracked values and
	# checking the instrument's reply puts them back, and a dummy driver has no reply - blanked
	# state stays blank, which is correct behaviour and an unavoidable failure here.
	if request.config.getoption("--dummy"):
		pytest.skip("refresh_state re-reads the instrument; there is nothing to re-read in dummy mode")

	_baseline(instrument, channel)
	instrument.set_frequency(channel, 2.5e3)
	instrument.set_amplitude(channel, 1.5)

	# Blank the tracked values so a stale reading cannot pass for a fresh one - without this the
	# setters above have already put the right answers in state and refresh_state could do nothing
	# at all and still look correct.
	for name in ("waveform_type", "frequency", "amplitude", "offset"):
		instrument.state.set(["channels", name], None, indices=[channel])

	instrument.refresh_state()

	state = instrument.state.channels[channel]

	assert state.waveform_type == BASELINE_WAVE, f"refresh_state left waveform_type as {state.waveform_type!r}"
	assert close_enough(2.5e3, state.frequency, rel=1e-4), f"refresh_state left frequency as {state.frequency!r}"
	assert close_enough(1.5, state.amplitude, rel=0.02, abs_tol=0.01), f"refresh_state left amplitude as {state.amplitude!r}"
	assert close_enough(BASELINE_OFFS, state.offset, abs_tol=0.01), f"refresh_state left offset as {state.offset!r}"
	assert state.output_enable is not None, "refresh_state did not read output_enable"
