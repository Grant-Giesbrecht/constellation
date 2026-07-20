""" Tests for plot_waveform()'s separateaxes option: one stacked subplot per waveform with a
synced (shared) X axis, instead of all waveforms layered on one set of axes. """

import matplotlib
matplotlib.use("Agg")  # headless - no display needed to run these tests

import pytest

from constellation.instrument_control.oscilloscope.oscilloscope_ctg import plot_waveform

def _waveforms():
	return [
		{"time_s": [0, 1, 2, 3], "volt_V": [0, 1, 0, -1], "channel": 1},
		{"time_s": [0, 1, 2, 3], "volt_V": [0, -1, 0, 1], "channel": 2},
	]

def test_default_mode_returns_single_axis_with_all_waveforms():
	ax = plot_waveform(_waveforms())
	assert len(ax.get_lines()) == 2

def test_separateaxes_returns_one_axis_per_waveform():
	axes = plot_waveform(_waveforms(), separateaxes=True)
	assert isinstance(axes, list)
	assert len(axes) == 2
	assert [len(a.get_lines()) for a in axes] == [1, 1]

def test_separateaxes_shares_x_limits():
	axes = plot_waveform(_waveforms(), separateaxes=True)
	axes[0].set_xlim(0.5, 2.5)
	assert axes[1].get_xlim() == (0.5, 2.5)

def test_separateaxes_with_single_waveform_still_returns_a_list():
	wf = _waveforms()[0]
	axes = plot_waveform(wf, separateaxes=True)
	assert isinstance(axes, list)
	assert len(axes) == 1

def test_separateaxes_rejects_explicit_axis():
	ax = plot_waveform(_waveforms()[0])
	with pytest.raises(ValueError):
		plot_waveform(_waveforms(), axis=ax, separateaxes=True)
