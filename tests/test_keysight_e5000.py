""" Tests for the Keysight E5071C VNA driver: dummy mode, the SCPI it emits, and trace handling.
Nothing here touches hardware - the SCPI strings are checked against the programmer's guide
from memory, not against an instrument. """

import numpy as np
import pytest
import pylogfile.base as plf

from constellation.relay import CommandRelay
from constellation.all import KeysightE5000, VNAChannelState, BasicVectorNetworkAnalyzerCtg as Ctg

def make_log():
	log = plf.LogPile()
	log.terminal_level = plf.CRITICAL
	return log

class _CannedRelay(CommandRelay):
	""" Replays canned replies matched by command prefix (longest wins); unlisted queries answer "0". """

	def __init__(self, table=None, binary=None):
		super().__init__()
		self.table = {"*IDN?": "Agilent Technologies,E5071C,MY46000000,A.11.01"}
		self.table.update(table or {})
		self.binary = binary or {}
		self.sent = []

	def connect(self): return True
	def close(self): pass
	def write(self, cmd): self.sent.append(cmd); return True
	def read(self): return True, ""

	def query(self, cmd):
		self.sent.append(cmd)
		for prefix in sorted(self.table, key=len, reverse=True):
			if cmd.startswith(prefix):
				return True, self.table[prefix]
		return True, "0"

	def query_binary(self, cmd, datatype='B'):
		self.sent.append(cmd)
		return True, list(self.binary.get(cmd, []))

def make_canned(table=None, binary=None):
	vna = KeysightE5000("canned", make_log(), relay=_CannedRelay(table, binary))
	vna.online = True
	return vna

def writes(vna):
	return [c for c in vna.relay.sent if "?" not in c]

# A 2-channel instrument: Ch1 has S21 + S11, Ch2 has one S22 trace.
TWO_CHANNELS = {
	"CALC1:PAR:COUN?": "+2", "CALC2:PAR:COUN?": "+1",
	"CALC1:PAR1:DEF?": "S21", "CALC1:PAR2:DEF?": "S11", "CALC2:PAR1:DEF?": "S22",
	"CALC1:SEL:FORM?": "MLOG", "CALC2:SEL:FORM?": "GDEL",
	"SENS1:FREQ:STAR?": "+1.0E+6", "SENS1:FREQ:STOP?": "+2.0E+9",
}

def test_set_freq_end_reads_back_the_end_frequency_not_the_start():
	""" Category bug: set_freq_end() used get_freq_start as its readback, so with real hardware
	the end-frequency state was filled with the START frequency. """
	vna = make_canned({"SENS1:FREQ:STAR?": "+1.0E+6", "SENS1:FREQ:STOP?": "+2.0E+9"})
	vna.state.channels[1] = VNAChannelState(log=vna.log)
	vna.set_freq_end(2e9)
	assert vna.state.get(["channels", "freq_end"], indices=[1]) == pytest.approx(2e9)

def test_every_vna_driver_has_one_get_rf_power():
	""" ZVA defined get_rf_power twice; the second (an OUTP:STAT query) silently won. """
	import inspect
	from constellation.all import RohdeSchwarzZVA
	src = inspect.getsource(RohdeSchwarzZVA)
	assert src.count("def get_rf_power") == 1

def test_constructs_and_reads_in_dummy():
	vna = KeysightE5000("DUMMY", make_log(), dummy=True)
	assert vna.get_freq_start() == pytest.approx(1e9)
	assert vna.get_rf_enable() is True
	data = vna.get_trace_data(vna.state.traces[vna.first_trace].id_str)
	assert len(data["x"]) == len(data["y"]) > 0

def test_scpi_for_channel_settings():
	vna = make_canned()
	vna.set_freq_start(1e6, channel=2)
	vna.set_num_points(401, channel=2)
	vna.set_res_bandwidth(1e3, channel=2)
	vna.set_power(-10, channel=2)
	vna.set_cal_enabled(True, channel=2)
	vna.set_rf_enable(False)
	
	sent = writes(vna)
	for cmd in ("SENS2:FREQ:STAR 1000000.0", "SENS2:SWE:POIN 401", "SENS2:BAND 1000.0",
			"SOUR2:POW -10", "SENS2:CORR:STAT ON", "OUTP OFF"):
		assert cmd in sent

def test_getters_parse_instrument_notation():
	vna = make_canned({"SENS1:SWE:POIN?": "+201", "SENS1:BAND?": "+1.0E+3", "OUTP?": "+1", "SENS1:CORR:STAT?": "+0"})
	assert vna.get_num_points() == 201
	assert vna.get_res_bandwidth() == pytest.approx(1e3)
	assert vna.get_rf_enable() is True
	assert vna.get_cal_enabled() is False

def test_refresh_discovers_channels_traces_formats():
	vna = make_canned(TWO_CHANNELS)
	vna.refresh_channels_and_traces()
	
	assert [i for i, _ in vna.state.channels.populated_items()] == [1, 2]
	traces = [t for t in vna.state.traces]
	assert [t.id_str for t in traces] == ["Ch1_Tr1", "Ch1_Tr2", "Ch2_Tr1"]
	assert [t.measurement for t in traces] == [Ctg.MEAS_S21, Ctg.MEAS_S11, Ctg.MEAS_S22]
	assert traces[0].format == Ctg.FORM_LOG_MAG
	assert traces[2].format == Ctg.FORM_OTHER # group delay has no category constant
	assert vna.state.get(["channels", "freq_start"], indices=[1]) == pytest.approx(1e6)

def test_add_trace_defines_the_next_trace_number():
	vna = make_canned(TWO_CHANNELS)
	vna.refresh_channels_and_traces()
	
	assert vna.add_trace(1, "mine", Ctg.MEAS_S12)
	sent = writes(vna)
	assert "CALC1:PAR:COUN 3" in sent
	assert "CALC1:PAR3:DEF S12" in sent
	assert vna._get_trace_idx("mine") is not None

def test_add_trace_rejections():
	vna = make_canned(TWO_CHANNELS)
	vna.refresh_channels_and_traces()
	before = len(vna.relay.sent)
	
	assert not vna.add_trace(1, "Ch1_Tr1", Ctg.MEAS_S11)  # duplicate name
	assert not vna.add_trace(1, "", Ctg.MEAS_S11)         # invalid name
	assert not vna.add_trace(5, "x", Ctg.MEAS_S11)        # no such channel
	assert not vna.add_trace(1, "x", "meas-bogus")        # unknown measurement
	assert len(vna.relay.sent) == before                  # nothing reached the instrument

def test_user_trace_names_survive_a_refresh():
	vna = make_canned(TWO_CHANNELS)
	vna.refresh_channels_and_traces()
	vna.add_trace(1, "mine", Ctg.MEAS_S12) # becomes Ch1 trace 3
	
	vna.relay.table.update({"CALC1:PAR:COUN?": "+3", "CALC1:PAR3:DEF?": "S12"}) # instrument now reports it
	vna.refresh_channels_and_traces()
	
	assert "mine" in [t.id_str for t in vna.state.traces]
	assert vna._trace_addr["mine"] == (1, 3)

def test_trace_data_is_complex_and_uses_the_instrument_stimulus_axis():
	raw = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0] # (re, im) x 3
	vna = make_canned(TWO_CHANNELS, binary={
		"CALC1:SEL:DATA:SDAT?": raw,
		"SENS1:FREQ:DATA?": [1e6, 5e8, 1e9],
	})
	vna.refresh_channels_and_traces()
	data = vna.get_trace_data("Ch1_Tr2")
	
	assert list(data["y"]) == [1+2j, 3+4j, 5+6j]
	assert data["x"] == [1e6, 5e8, 1e9]
	sent = vna.relay.sent
	assert sent.index("CALC1:PAR2:SEL") < sent.index("CALC1:SEL:DATA:SDAT?")
	assert "FORM:DATA REAL" in sent and "FORM:BORD SWAP" in sent

def test_trace_data_for_missing_trace_is_none():
	vna = make_canned(TWO_CHANNELS)
	vna.refresh_channels_and_traces()
	assert vna.get_trace_data("nope") is None

def test_clear_traces_resets_each_channel_to_one_trace():
	vna = make_canned(TWO_CHANNELS)
	vna.refresh_channels_and_traces()
	vna.clear_traces()
	
	sent = writes(vna)
	assert "CALC1:PAR:COUN 1" in sent and "CALC2:PAR:COUN 1" in sent
