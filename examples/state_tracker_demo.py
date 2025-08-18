from constellation.base import *

class DemoA():
	
	def __init__(self):
		self.a = 1
		self.b = 2
		self.c = 3

class DemoB():
	
	def __init__(self):
		self.X = IndexedList(1, 4)
		self.z = 10

class DemoState(InstrumentState):
	def __init__(self, log):
		super().__init__(log=log)
		self.time = DemoA()
		self.volt = DemoB()
		self.chans = IndexedList(1, 4)

log = plf.LogPile()
ds = DemoState(log)

#============= Test the set functions ==============

# First, a simple example
ds.set(["time", "a"], 100)
print(f"100 = ds.time.a = {ds.time.a}")

# Next show that INdexedLists can be populated in-situ
ds.set(["chans"], DemoA(), indices=[1])
print(f"DemoA = {ds.chans.get_idx_val(1)}")

# Now show how IndexedList's objects can be edited
ds.set(["chans", "c"], 17, [1])
print(f"17 = {ds.chans.get_idx_val(1).c}")


#============= Test the get functions ==============

ds.get(["time", "a"], )
ds.get(["chans"], indices=[1])
ds.get(["chans", "c"], [1])