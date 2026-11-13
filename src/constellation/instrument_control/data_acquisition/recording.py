''' Recording a DataAcquisition stream to disk, as HDF5.

	with HDF5StreamRecorder("run.h5", daq) as rec:
		daq.stream(rec.write, duration_s=3600, channels=[0, 1], chunk_samples=1000)

Driver-agnostic: it uses only what every DataAcquisition driver provides - the chunks from
read_stream(), stream_info() and the device identity - so any DAQ driver records with it unchanged.

FILE LAYOUT

The file is a tome (stardust.tome; spec in stardust's docs/tome/format.md): every value is a
dataset or group tagged with a `__pytype__` attribute, and nothing else is stored in attributes. So
`stardust.tome.tome_to_dict(path)` returns the whole recording as a dict, and MATLAB reads any
field directly, e.g. `h5read(path, '/ai0/data')` or `h5read(path, '/dt_s')`.

By default the recording is the whole file. With `group=`, it is one entry of a larger tome
instead - a new file, or one that already exists (see WRITING INTO AN EXISTING TOME):

	dict_to_tome({"x": x, "y": y}, "run.tome")
	with HDF5StreamRecorder("run.tome", daq, group="DAQ_data/Daq1") as rec: ...
	tome_to_dict("run.tome")    # {"x": ..., "y": ..., "DAQ_data": {"Daq1": {"dt_s": ..., "ai0": ...}}}

The layout below then starts at that group rather than at `/`.

	/                    dict
	  format             str     "constellation-daq-stream"
	  format_version     int
	  created_at         str     ISO 8601, UTC
	  start_time         str     ISO 8601, UTC - when sample 0 was taken    } from the first chunk
	  sample_rate_Hz     float   the rate the board actually used           }
	  dt_s               float                                              }
	  first_sample       int     stream index of data[0]                    }
	  chunk_samples      int                                                }
	  channels           list    of int, e.g. [0, 1]                        }
	  device_identity    str                                                }
	  driver             str                                                }
	  ai<n>/             dict    one per streamed channel                   }
	    data             ndarray float64 [samples], grows while recording
	    units            str     "V"
	    terminal_config  str
	    range_min_V      float
	    range_max_V      float
	  end_time           str     } written at close
	  samples_written    int     }
	  stop_reason        str     }
	  complete           bool    } false if the disk writer failed
	  writer_error       str     } only if it did

There is no time column: `data[k]` is stream sample `first_sample + k`, at
`(first_sample + k) * dt_s` seconds after `start_time`. Storing time would cost a third more space
and say nothing those two values don't.

The `data` datasets are chunked and resizable, unlike those dict_to_tome() writes; the format
allows any HDF5 layout, and readers don't see the difference.

WHY A WRITER THREAD. write() only queues the chunk and returns, so the acquisition loop never
waits on the disk. Disk writes stall occasionally (antivirus, a network drive, the OS flushing its
cache) and a stall inside the stream loop would stop it draining the DAQ's own buffer. The queue is
bounded by `max_backlog_s` of signal: a disk that falls behind for good makes write() raise, which
stops the stream, instead of growing memory until something else gives.

CRASH SAFETY. Plain HDF5 is not journaled - a process killed mid-write can leave a file
unreadable. The file is therefore written in SWMR (single-writer/multiple-reader) mode, in which it
stays valid at every flush; a hard crash loses at most the last `flush_interval_s` of data. SWMR
also lets another process watch a recording live:

	f = h5py.File("run.h5", "r", libver="latest", swmr=True)
	ds = f["ai0/data"]; ds.refresh(); latest = ds[-1000:]

SWMR forbids creating anything new while writing, so the closing values (end_time,
samples_written, stop_reason, complete) are added by reopening the file once the writer is done. A
recording without `complete` was not closed cleanly - its data is still valid up to the last flush.

WRITING INTO AN EXISTING TOME. The file is opened for appending and the rest of it is left alone.
Some things to know:
  - The file must have HDF5 superblock v3 (libver "latest"), which SWMR requires. Tomes from the
    current stardust dict_to_tome have it; ones from stardust 0.2.0 and earlier don't, and are
    refused at open() - rewrite one with the current dict_to_tome, or record to a separate file.
  - Every group on the path must be a tome dict: a dataset or a list_of_dicts in the way is
    refused, since adding a named entry there would change what the document means.
  - While recording, the whole file is locked to this writer: nothing else, in this process or
    another, can write to it until close(). And dict_to_tome() replaces the whole file, so never
    call it on that path afterwards - add to the file with stardust's write_tome_value instead.
  - SWMR keeps the file valid through a crash, the existing entries included. A separate file per
    recording is still the zero-risk choice for data that can't be regenerated.
'''

import datetime
import math
import os
import queue
import threading
import time

import h5py
import numpy as np
from stardust.tome import TYPE_ATTR, require_tome_group, write_tome_value

FILE_FORMAT = "constellation-daq-stream"
FORMAT_VERSION = 1

# HDF5 storage chunk size bounds, in samples. Independent of the stream's chunk size: tiny storage
# chunks make large files slow to read, huge ones waste space on a short recording.
_MIN_STORAGE_CHUNK = 8192
_MAX_STORAGE_CHUNK = 1 << 20

_IDLE = object()     # writer-queue timeout marker

class RecorderError(RuntimeError):
	''' The recording could not continue. Raised from write() so that the stream stops too. '''

def _utc_now() -> datetime.datetime:
	return datetime.datetime.now(datetime.timezone.utc)

def _put(group, key:str, value) -> None:
	''' Writes one tome value. numpy scalars become Python ones, so they are tagged int/float
	like everything else in the file rather than int64/float64. '''
	if isinstance(value, np.generic):
		value = value.item()
	write_tome_value(group, key, value)

class HDF5StreamRecorder:
	''' Writes a DataAcquisition stream to an HDF5 file, from a background thread.

	Use as a context manager around daq.stream(rec.write, ...), or call open()/close() yourself
	around a start_stream()/read_stream() loop that passes each chunk to write(). One recorder
	holds one contiguous stream: a chunk that doesn't follow on from the previous one - a gap, or
	a second stream - raises rather than being written as if it were continuous.
	'''

	def __init__(self, path, daq, group:str="/", flush_interval_s:float=1.0, max_backlog_s:float=10.0,
			overwrite:bool=False):
		'''
		Args:
			path (str|PathLike): File to write. With the default `group`, a new file; otherwise new
				or an existing tome.
			daq (DataAcquisition): The driver being streamed, for its metadata.
			group (str): Where in the file the recording goes, e.g. "DAQ_data/Daq1". Missing groups
				on the path are created. Default: the whole file.
			flush_interval_s (float): How often the writer makes written data durable and visible
				to SWMR readers. Bounds what a hard crash can lose.
			max_backlog_s (float): How much signal may wait in the queue before write() gives up.
			overwrite (bool): Replace what is already at the target - the whole file for the
				default group, only that group otherwise. Off by default: a recording is data.
		'''

		self.path = os.fspath(path)
		self.daq = daq
		self.group = "/" + "/".join(part for part in str(group).split("/") if part)
		self.flush_interval_s = float(flush_interval_s)
		self.max_backlog_s = float(max_backlog_s)
		self.overwrite = overwrite

		self.samples_written = 0       # by the writer thread
		self._next_sample = None       # caller side: the first_sample the next chunk must have
		self._channels = None
		self._file = None
		self._root = None              # the recording's group: the file itself, or self.group in it
		self._datasets = {}
		self._queue = None
		self._thread = None
		self._error = None
		self._closed = False

	# ---------------------------------------------------------------------------------------
	# Lifecycle
	# ---------------------------------------------------------------------------------------

	def open(self):
		''' Creates the file, or the recording's group in an existing one. Returns self.

		Raises:
			FileExistsError: The target (the file, or the group) exists and overwrite is False.
			ValueError: An existing file can't take a SWMR recording (superblock older than v3).
			TypeError: Something on the group's path is not a tome dict.
		'''

		whole_file = self.group == "/"
		exists = os.path.exists(self.path)
		if whole_file and exists and not self.overwrite:
			raise FileExistsError(f"{self.path} already exists - pass overwrite=True to replace it, "
				f"or group= to record into it.")

		if whole_file or not exists:
			f = h5py.File(self.path, "w", libver="latest")
			f.attrs[TYPE_ATTR] = "dict"
		else:
			f = h5py.File(self.path, "a", libver="latest")

		try:
			if f.id.get_create_plist().get_version()[0] < 3:
				raise ValueError(f"{self.path} can't take a SWMR recording: it was created without "
					f"libver='latest' (HDF5 superblock v3). Rewrite it with stardust's current dict_to_tome, "
					f"or record to a separate file.")
			if not whole_file and self.group in f:
				if not self.overwrite:
					raise FileExistsError(f"{self.path} already has {self.group} - pass overwrite=True to "
						f"replace it, or choose another group.")
				del f[self.group]
			self._root = require_tome_group(f, self.group)
			_put(self._root, "format", FILE_FORMAT)
			_put(self._root, "format_version", FORMAT_VERSION)
			_put(self._root, "created_at", _utc_now().isoformat())
		except BaseException:
			f.close()
			raise

		self._file = f
		return self

	def __enter__(self):
		return self.open()

	def __exit__(self, exc_type, exc, tb):

		if exc_type is None:
			self.close()
			return False

		reason = "interrupted" if issubclass(exc_type, KeyboardInterrupt) else f"{exc_type.__name__}: {exc}"
		try:
			self.close(stop_reason=reason)
		except RecorderError:
			pass       # the exception already propagating is the one worth seeing
		return False

	def close(self, stop_reason:str=None) -> None:
		''' Drains the queue, finishes the file and adds its closing values.

		Args:
			stop_reason (str): Why the recording ended. Default: the reason the driver gives for
				its stream ending.

		Raises:
			RecorderError: If the writer failed. The file is still finished as far as it got,
				marked complete=False.
		'''

		if self._closed or self._file is None:
			return
		self._closed = True

		if self._thread is not None:
			# put() can't block forever on a full queue if the writer died: keep trying only while
			# it is alive to drain it.
			while self._thread.is_alive():
				try:
					self._queue.put(None, timeout=0.2)
					break
				except queue.Full:
					continue
			self._thread.join()

		self._file.close()

		if stop_reason is None:
			if getattr(self.daq, "is_streaming", lambda: False)():
				stop_reason = "recorder closed while the stream was still running"
			elif self._thread is not None and getattr(self.daq, "last_stream_end", None):
				stop_reason = self.daq.last_stream_end["reason"]
			else:
				stop_reason = "closed"

		with h5py.File(self.path, "a", libver="latest") as f:
			g = f[self.group]
			_put(g, "end_time", _utc_now().isoformat())
			_put(g, "samples_written", self.samples_written)
			_put(g, "stop_reason", stop_reason)
			_put(g, "complete", self._error is None)
			if self._error is not None:
				_put(g, "writer_error", f"{type(self._error).__name__}: {self._error}")

		if self._error is not None:
			raise RecorderError(f"writing {self.path} failed after {self.samples_written} samples") from self._error

	# ---------------------------------------------------------------------------------------
	# Producer side (the stream loop's thread)
	# ---------------------------------------------------------------------------------------

	def write(self, chunk:dict) -> None:
		''' Queues one read_stream() chunk for writing. A `daq.stream()` callback.

		Raises:
			RecorderError: If the writer has failed, has fallen more than max_backlog_s behind, or
				the chunk does not continue the recording.
		'''

		if self._file is None or self._closed:
			raise RecorderError("the recorder is not open")
		if self._error is not None:
			raise RecorderError(f"the disk writer failed: {self._error}") from self._error

		if self._thread is None:
			self._begin(chunk)

		if chunk["first_sample"] != self._next_sample:
			raise RecorderError(f"chunk starts at sample {chunk['first_sample']}, expected {self._next_sample} - "
				f"a gap or a different stream. One recorder holds one contiguous stream.")
		if set(chunk["channels"]) != set(self._channels):
			raise RecorderError(f"chunk has channels {sorted(chunk['channels'])}, recording has {self._channels}")

		# Copied, so a caller that reuses or edits its arrays after the callback can't change what
		# reaches the disk.
		arrays = {ch: np.array(v, dtype=np.float64) for ch, v in chunk["channels"].items()}

		try:
			self._queue.put_nowait(arrays)
		except queue.Full:
			raise RecorderError(f"the disk writer is more than {self.max_backlog_s:g} s behind the stream - "
				f"the disk is too slow for this sample rate") from None

		self._next_sample += len(chunk["t_s"])

	def _begin(self, chunk:dict) -> None:
		''' Lays out the file from the first chunk, switches it to SWMR, and starts the writer.

		Everything but the closing values has to exist before SWMR is enabled, and
		the first chunk is the earliest point the channel list and rate are certain. '''

		n = len(chunk["t_s"])
		dt = float(chunk["dt_s"])
		info = self.daq.stream_info() if hasattr(self.daq, "stream_info") else None
		self._channels = sorted(chunk["channels"])
		self._next_sample = int(chunk["first_sample"])

		f = self._root
		start = info["started_at"].isoformat() if info else _utc_now().isoformat()
		_put(f, "start_time", start)
		_put(f, "sample_rate_Hz", 1.0 / dt)
		_put(f, "dt_s", dt)
		_put(f, "first_sample", self._next_sample)
		_put(f, "chunk_samples", n)
		_put(f, "channels", [int(ch) for ch in self._channels])
		_put(f, "device_identity", str(getattr(getattr(self.daq, "id", None), "idn_model", "") or ""))
		_put(f, "driver", type(self.daq).__name__)

		storage_chunk = min(max(n, _MIN_STORAGE_CHUNK), _MAX_STORAGE_CHUNK)
		for ch in self._channels:
			grp = f.create_group(f"ai{ch}")
			grp.attrs[TYPE_ATTR] = "dict"
			ds = grp.create_dataset("data", shape=(0,), maxshape=(None,), dtype="f8", chunks=(storage_chunk,))
			ds.attrs[TYPE_ATTR] = "ndarray"
			ds.attrs["dtype"] = "float64"
			_put(grp, "units", "V")
			for key, value in ((info or {}).get("channels", {}).get(ch) or {}).items():
				_put(grp, key, value)
			self._datasets[ch] = ds

		self._file.swmr_mode = True

		chunk_s = n * dt
		depth = max(2, math.ceil(self.max_backlog_s / chunk_s)) if chunk_s > 0 else 100
		self._queue = queue.Queue(maxsize=depth)
		self._thread = threading.Thread(target=self._run, name=f"HDF5StreamRecorder({os.path.basename(self.path)})", daemon=True)
		self._thread.start()

	# ---------------------------------------------------------------------------------------
	# Writer thread - the only thing touching the file between _begin() and close()
	# ---------------------------------------------------------------------------------------

	def _run(self) -> None:

		last_flush = time.monotonic()
		try:
			while True:
				try:
					item = self._queue.get(timeout=self.flush_interval_s)
				except queue.Empty:
					item = _IDLE

				if item is None:
					break

				if item is not _IDLE:
					self._append(item)

				if time.monotonic() - last_flush >= self.flush_interval_s:
					self._flush()
					last_flush = time.monotonic()

			self._flush()
		except BaseException as e:
			self._error = e

	def _append(self, arrays:dict) -> None:
		n = len(next(iter(arrays.values())))
		end = self.samples_written + n
		for ch, values in arrays.items():
			ds = self._datasets[ch]
			ds.resize((end,))
			ds[self.samples_written:end] = values
		self.samples_written = end

	def _flush(self) -> None:
		for ds in self._datasets.values():
			ds.flush()
		self._file.flush()
