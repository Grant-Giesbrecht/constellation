""" Demonstrates automatic InstrumentState validation.

Every InstrumentState subclass validates itself right after construction (see
InstrumentState.__init_subclass__ in base.py): it cross-checks stardust's class-level
`__state_fields__` serialization manifest against the per-instance registry built by
`add_param()`. Drift between the two means a parameter silently does not serialize.

Nothing here calls validate() - constructing the driver builds its whole state tree, and each
state object in it validates on the way up. A clean driver is silent; the per-parameter detail is
logged at LOWDEBUG, which is why the terminal level is set below. Reporting goes through the log
only, never to stdout.
"""

from constellation.instrument_control.all import *

log = plf.LogPile()
log.set_terminal_level("LOWDEBUG")

sa = SiglentSSA3000X("GPIB::17::INTR", log, dummy=True)

# What drift looks like. `stray` is registered with add_param() but missing from
# __state_fields__, so it would silently fail to serialize - constructing this logs a warning
# naming the parameter and which list it's missing from.
class DriftingExampleState(InstrumentState):

	__state_fields__ = ("tracked",)

	def __init__(self, log:plf.LogPile=None):
		super().__init__(log=log)

		self.add_param("tracked", unit="V")
		self.add_param("stray", unit="V")

_ = DriftingExampleState(log=log)
