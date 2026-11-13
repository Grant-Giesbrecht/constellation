""" HDF5StreamRecorder: a DataAcquisition stream written to disk.

Uses the stub DAQ from test_data_acquisition.py, whose stream is a ramp across the whole
recording, so a dropped, duplicated or reordered chunk shows up as a broken ramp in the file.
"""

import subprocess
import sys
import textwrap
import time

import h5py
import numpy as np
import pytest
from stardust.tome import dict_to_tome, tome_to_dict

from constellation.instrument_control.data_acquisition.data_acquisition_ctg import DataAcquisition
from constellation.instrument_control.data_acquisition.recording import HDF5StreamRecorder, RecorderError

from test_data_acquisition import _StubDAQ

def _daq(rate=1000.0):
	daq = _StubDAQ()
	daq.set_sample_rate(rate)
	daq.set_ai_channel(0, -10.0, 10.0, DataAcquisition.TERM_RSE)
	daq.set_ai_channel(2, -1.0, 1.0, DataAcquisition.TERM_NRSE)
	return daq

def _chunk(first, n, channels=(0,), dt=1e-3):
	return {"first_sample": first, "dt_s": dt, "t_s": (first + np.arange(n)) * dt,
		"channels": {ch: np.arange(first, first + n, dtype=float) for ch in channels}}

def test_a_recording_round_trips_with_its_metadata(tmp_path):
	path = tmp_path / "run.h5"
	daq = _daq()

	with HDF5StreamRecorder(path, daq) as rec:
		assert daq.stream(rec.write, max_chunks=5, channels=[0, 2], chunk_samples=100) == 500

	# Read back through stardust's own tome reader: the file is a tome, so it comes back as a dict
	# with every value its proper type.
	d = tome_to_dict(str(path))
	for ch in (0, 2):
		assert d[f"ai{ch}"]["data"].dtype == np.float64
		assert d[f"ai{ch}"]["data"].tolist() == [float(k) for k in range(500)]
	assert d["format"] == "constellation-daq-stream" and d["format_version"] == 1
	assert d["dt_s"] == pytest.approx(1e-3) and d["sample_rate_Hz"] == pytest.approx(1000.0)
	assert d["first_sample"] == 0 and d["chunk_samples"] == 100
	assert d["channels"] == [0, 2]
	assert d["device_identity"] == "Vendor,STUB,0001,1.0" and d["driver"] == "_StubDAQ"
	assert d["samples_written"] == 500 and d["complete"] is True
	assert d["stop_reason"] == "max_chunks=5 reached"
	assert d["start_time"] <= d["end_time"]
	assert d["ai2"] | {"data": None} == {"data": None, "units": "V", "terminal_config": "nrse",
		"range_min_V": -1.0, "range_max_V": 1.0}
	assert "writer_error" not in d

def test_the_file_follows_the_tome_rules(tmp_path):
	""" Every node tagged, and no user data in attributes - the rules that make a tome loadable
	without knowing its layout (MATLAB's h5read included). """
	path = tmp_path / "run.h5"
	with HDF5StreamRecorder(path, _daq()) as rec:
		rec.write(_chunk(0, 10, channels=(0, 2)))

	allowed = {"__pytype__", "dtype", "elem_encoding"}
	nodes = []
	with h5py.File(path, "r") as f:
		nodes.append(f)
		f.visititems(lambda name, node: nodes.append(node))
		for node in nodes:
			assert "__pytype__" in node.attrs, node.name
			assert set(node.attrs) <= allowed, (node.name, set(node.attrs) - allowed)
		assert f["/"].attrs["__pytype__"] == "dict"
		assert f["ai0/data"].attrs["__pytype__"] == "ndarray" and f["ai0/data"].attrs["dtype"] == "float64"

def test_an_existing_file_is_not_overwritten_by_default(tmp_path):
	path = tmp_path / "run.h5"
	path.write_bytes(b"precious")
	with pytest.raises(FileExistsError):
		HDF5StreamRecorder(path, _daq()).open()
	assert path.read_bytes() == b"precious"

	with HDF5StreamRecorder(path, _daq(), overwrite=True) as rec:
		rec.write(_chunk(0, 10))
	assert tome_to_dict(str(path))["samples_written"] == 10

def test_an_interrupted_recording_keeps_its_data_and_says_why(tmp_path):
	path = tmp_path / "run.h5"
	daq = _daq()

	def stop_after_three(chunk):
		rec.write(chunk)
		if chunk["first_sample"] == 20:
			raise KeyboardInterrupt

	with pytest.raises(KeyboardInterrupt):
		with HDF5StreamRecorder(path, daq) as rec:
			daq.stream(stop_after_three, channels=[0], chunk_samples=10)

	assert not daq.is_streaming()
	d = tome_to_dict(str(path))
	assert d["ai0"]["data"].tolist() == [float(k) for k in range(30)]
	assert d["stop_reason"] == "interrupted" and d["complete"] is True

def test_a_gap_is_refused_not_written_as_continuous(tmp_path):
	with HDF5StreamRecorder(tmp_path / "run.h5", _daq()) as rec:
		rec.write(_chunk(0, 10))
		with pytest.raises(RecorderError, match="expected 10"):
			rec.write(_chunk(20, 10))
		with pytest.raises(RecorderError, match="channels"):
			rec.write(_chunk(10, 10, channels=(0, 1)))

def test_a_disk_that_falls_behind_stops_the_stream(tmp_path, monkeypatch):
	daq = _daq(rate=1000.0)
	rec = HDF5StreamRecorder(tmp_path / "run.h5", daq, max_backlog_s=0.05)     # 5 chunks of 10 ms
	slow = rec._append
	monkeypatch.setattr(rec, "_append", lambda arrays: (time.sleep(0.5), slow(arrays)))

	with pytest.raises(RecorderError, match="behind"):
		with rec:
			daq.stream(rec.write, channels=[0], chunk_samples=10)
	assert not daq.is_streaming()

def test_a_writer_failure_surfaces_and_marks_the_file_incomplete(tmp_path, monkeypatch):
	path = tmp_path / "run.h5"
	daq = _daq()
	rec = HDF5StreamRecorder(path, daq)

	def disk_full(arrays):
		raise OSError("disk full")

	monkeypatch.setattr(rec, "_append", disk_full)

	with pytest.raises(RecorderError, match="disk full"):
		with rec:
			daq.stream(lambda c: (rec.write(c), time.sleep(0.01)), channels=[0], chunk_samples=10)

	assert not daq.is_streaming()
	d = tome_to_dict(str(path))
	assert d["complete"] is False and "disk full" in d["writer_error"]

_SWMR_READER = textwrap.dedent("""
	import sys, h5py
	with h5py.File(sys.argv[1], "r", libver="latest", swmr=True) as f:
		ds = f["ai0/data"]
		ds.refresh()
		print(len(ds), ds[-1] if len(ds) else "")
""")

def test_another_process_can_watch_a_recording_live(tmp_path):
	path = tmp_path / "run.h5"
	with HDF5StreamRecorder(path, _daq(), flush_interval_s=0.05) as rec:
		for k in range(5):
			rec.write(_chunk(k * 100, 100))
		time.sleep(0.4)            # several flush intervals
		out = subprocess.run([sys.executable, "-c", _SWMR_READER, str(path)], capture_output=True, text=True, timeout=60)
		assert out.returncode == 0, out.stderr
		assert out.stdout.split() == ["500", "499.0"]
		rec.write(_chunk(500, 100))

	assert len(tome_to_dict(str(path))["ai0"]["data"]) == 600

_CRASHING_WRITER = textwrap.dedent("""
	import os, sys, time
	import numpy as np
	from constellation.instrument_control.data_acquisition.recording import HDF5StreamRecorder

	class Daq:
		pass

	rec = HDF5StreamRecorder(sys.argv[1], Daq(), flush_interval_s=0.05).open()
	for k in range(10):
		rec.write({"first_sample": k * 100, "dt_s": 1e-3, "t_s": np.arange(100) * 1e-3,
			"channels": {0: np.arange(k * 100, k * 100 + 100, dtype=float)}})
	time.sleep(0.5)
	os._exit(1)                # killed: no close(), no flush, no atexit
""")

def test_a_writer_killed_mid_recording_leaves_a_readable_file(tmp_path):
	""" The claim the SWMR layout exists to keep: a hard crash loses at most the last flush
	interval, not the file. """
	path = tmp_path / "crashed.h5"
	out = subprocess.run([sys.executable, "-c", _CRASHING_WRITER, str(path)], capture_output=True, text=True, timeout=60)
	assert out.returncode == 1, out.stderr

	with h5py.File(path, "r", libver="latest", swmr=True) as f:
		assert f["ai0/data"][:].tolist() == [float(k) for k in range(1000)]
		assert "complete" not in f                # how a reader tells it wasn't closed cleanly

# ---------------------------------------------------------------------------------------------
# group=: recording as one entry of a larger tome
# ---------------------------------------------------------------------------------------------

def test_a_recording_goes_into_a_group_of_an_existing_tome(tmp_path):
	path = str(tmp_path / "run.tome")
	assert dict_to_tome({"x": np.arange(3.0), "notes": "bench 2", "DAQ_data": {"Daq0": {"kept": 1}}}, path)
	daq = _daq()

	with HDF5StreamRecorder(path, daq, group="DAQ_data/Daq1") as rec:
		daq.stream(rec.write, max_chunks=3, channels=[0, 2], chunk_samples=10)

	d = tome_to_dict(path)
	assert d["x"].tolist() == [0.0, 1.0, 2.0] and d["notes"] == "bench 2"
	assert d["DAQ_data"]["Daq0"] == {"kept": 1}
	r = d["DAQ_data"]["Daq1"]
	assert r["ai2"]["data"].tolist() == [float(k) for k in range(30)]
	assert r["channels"] == [0, 2] and r["samples_written"] == 30 and r["complete"] is True
	assert r["stop_reason"] == "max_chunks=3 reached"
	assert "complete" not in d and "ai0" not in d          # nothing leaked to the root

def test_a_group_in_a_new_file_creates_the_file(tmp_path):
	path = str(tmp_path / "new.tome")
	with HDF5StreamRecorder(path, _daq(), group="/a/b/") as rec:
		rec.write(_chunk(0, 10))
	d = tome_to_dict(path)
	assert list(d) == ["a"] and d["a"]["b"]["samples_written"] == 10

def test_an_existing_group_is_refused_unless_overwrite(tmp_path):
	path = str(tmp_path / "run.tome")
	assert dict_to_tome({"x": 1, "rec": {"old": True}}, path)

	with pytest.raises(FileExistsError, match="/rec"):
		HDF5StreamRecorder(path, _daq(), group="rec").open()
	assert tome_to_dict(path) == {"x": 1, "rec": {"old": True}}

	with HDF5StreamRecorder(path, _daq(), group="rec", overwrite=True) as rec:
		rec.write(_chunk(0, 10))
	d = tome_to_dict(path)
	assert d["x"] == 1 and "old" not in d["rec"] and d["rec"]["samples_written"] == 10

@pytest.mark.parametrize("group", ["x/rec", "events/rec"])
def test_a_non_dict_on_the_path_is_refused(tmp_path, group):
	path = str(tmp_path / "run.tome")
	assert dict_to_tome({"x": 1, "events": [{"t": 0.1}]}, path)
	with pytest.raises(TypeError):
		HDF5StreamRecorder(path, _daq(), group=group).open()
	assert tome_to_dict(path) == {"x": 1, "events": [{"t": 0.1}]}

def test_an_old_superblock_file_is_refused_before_anything_is_written(tmp_path):
	path = str(tmp_path / "old.h5")
	with h5py.File(path, "w") as f:           # h5py's default libver: superblock v0, no SWMR
		f.attrs["__pytype__"] = "dict"
	with pytest.raises(ValueError, match="superblock"):
		HDF5StreamRecorder(path, _daq(), group="rec").open()
	with h5py.File(path, "r") as f:
		assert list(f) == []

def test_a_group_recording_can_be_watched_live(tmp_path):
	path = str(tmp_path / "run.tome")
	assert dict_to_tome({"x": 1}, path)
	reader = _SWMR_READER.replace('f["ai0/data"]', 'f["DAQ_data/Daq1/ai0/data"]')
	with HDF5StreamRecorder(path, _daq(), group="DAQ_data/Daq1", flush_interval_s=0.05) as rec:
		for k in range(5):
			rec.write(_chunk(k * 100, 100))
		time.sleep(0.4)
		out = subprocess.run([sys.executable, "-c", reader, path], capture_output=True, text=True, timeout=60)
		assert out.returncode == 0, out.stderr
		assert out.stdout.split() == ["500", "499.0"]

_CRASHING_GROUP_WRITER = textwrap.dedent("""
	import os, sys, time
	import numpy as np
	from stardust.tome import dict_to_tome
	from constellation.instrument_control.data_acquisition.recording import HDF5StreamRecorder

	class Daq:
		pass

	dict_to_tome({"x": np.arange(5.0), "notes": "precious"}, sys.argv[1])
	rec = HDF5StreamRecorder(sys.argv[1], Daq(), group="DAQ_data/Daq1", flush_interval_s=0.05).open()
	for k in range(10):
		rec.write({"first_sample": k * 100, "dt_s": 1e-3, "t_s": np.arange(100) * 1e-3,
			"channels": {0: np.arange(k * 100, k * 100 + 100, dtype=float)}})
	time.sleep(0.5)
	os._exit(1)
""")

def test_a_crash_while_recording_into_a_tome_keeps_the_existing_entries(tmp_path):
	path = tmp_path / "crashed.tome"
	out = subprocess.run([sys.executable, "-c", _CRASHING_GROUP_WRITER, str(path)], capture_output=True, text=True, timeout=60)
	assert out.returncode == 1, out.stderr

	with h5py.File(path, "r", libver="latest", swmr=True) as f:
		assert f["x"][:].tolist() == [0.0, 1.0, 2.0, 3.0, 4.0]
		assert f["DAQ_data/Daq1/ai0/data"][:].tolist() == [float(k) for k in range(1000)]
		assert "complete" not in f["DAQ_data/Daq1"]
	d = tome_to_dict(str(path))
	assert d["notes"] == "precious"
