from constellation import *

sa1 = SpectrumAnalyzerRemote(remote_id="RohdeSchwarz_FSQ")

sa1.set_freq_end(1e9)


osc = RemoteInstrument(remote_address="192.168.0.10|192.168.0.14")
osc.remote_call("set_freq_end", 1e9)