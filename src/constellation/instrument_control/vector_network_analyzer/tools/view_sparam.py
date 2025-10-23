#!/usr/bin/env python

import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
from scipy.optimize import curve_fit
from colorama import Fore, Style
from scipy.signal import hilbert
from scipy.signal import butter, lfilter, freqz
import time
import os
import sys
import argparse
import mplcursors

from pylogfile.base import mdprint
from constellation.helpers import lin_to_dB
from jarnsaxa import hdf_to_dict

from sparam_helpers import *

parser = argparse.ArgumentParser()
parser.add_argument('filename')
parser.add_argument('--ymin', help='Zero-crossing analysis plot, minimum Y value', type=float)
parser.add_argument('--ymax', help='Zero-crossing analysis plot, maximum Y value', type=float)
parser.add_argument('--xmin', help='Zero-crossing analysis plot, minimum X value', type=float)
parser.add_argument('--xmax', help='Zero-crossing analysis plot, maximum X value', type=float)
args = parser.parse_args()

# Load s-parameter data
sp = SParams(args.filename)

# Create figure
fig1 = plt.figure(1, figsize=(8, 8))
gs1 = fig1.add_gridspec(1, 1)
ax1a = fig1.add_subplot(gs1[0, 0])

plot_params = sp.available_parameters()
color_definitions = {"S11":"tab:blue", "S22":"tab:orange", "S12":"tab:green", "S21":"tab:red"}

for param in plot_params:
	# NOTE: plot_vna_mag() can be imported from Constellation and used as a shorthand for plotting S-parameters
	plt.plot(sp.get_frequency(param)/1e9, sp.get_parameter(param), label=param, color=color_definitions[param])
	
ax1a.set_xlabel("Frequency (GHz)")
ax1a.set_ylabel("dB")
ax1a.grid(True)
ax1a.set_title(os.path.basename(args.filename))

# Set Y-limits
if (args.ymin is not None) and (args.ymax is not None):
	ax1a.set_ylim([args.ymin, args.ymax])
elif (args.ymin is not None):
	ym = ax1a.get_ylim()
	ax1a.set_ylim([args.ymin, ym[1]])
elif (args.ymax is not None):
	ym = ax1a.get_ylim()
	ax1a.set_ylim([ym[0], args.ymax])

# Set x-limits
if (args.xmin is not None) and (args.xmax is not None):
	ax1a.set_xlim([args.xmin, args.xmax])
elif (args.xmin is not None):
	xm = ax1a.get_xlim()
	ax1a.set_xlim([args.xmin, xm[1]])
elif (args.xmax is not None):
	xm = ax1a.get_xlim()
	ax1a.set_xlim([xm[0], args.xmax])


# Print file info if available
if sp.metadata:
	try:
		dts = sp.metadata['timestamp']
	except:
		dts = None
	
	icn = sp.metadata['cal_notes']
	ign = sp.metadata['gen_notes']
	
	mdprint(f">:aFile info<:")
	mdprint(f"\t >Calibration Notes<: @:LOCK{icn}@:UNLOCK")
	mdprint(f"\t >General Notes<: @:LOCK{ign}@:UNLOCK")
	if dts is not None:
		mdprint(f"\t >Timestamp<: @:LOCK{dts}@:UNLOCK")



mplcursors.cursor(multiple=True)

ax1a.legend()
fig1.tight_layout()

plt.show()