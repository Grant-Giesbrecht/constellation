# mything.py
from __future__ import annotations
from datetime import datetime, timezone
import numpy as np

from stateclass import SerializableMixin
from serializer import dump_state, load_state
from base import Foo, Bar  # these can also inherit SerializableMixin to auto-register

class MyThing(SerializableMixin):
    __state_fields__   = ("when", "config", "foo", "bar", "weights", "misc")
    __schema_version__ = 2  # bump when persisted attributes change

    def __init__(self):
        self.when = datetime(2025, 8, 28, 12, 34, 56, tzinfo=timezone.utc)
        self.config = {"threshold": 0.7}
        self.foo = Foo(1, "hello")
        self.bar = Bar("series", [1, 2, 3])
        self.weights = np.array([[1.0, 2.0], [3.0, 4.0]])
        self.misc = [{"nested": Foo(42, "deep")}]

    def __post_deserialize__(self):
        # any fixups / validation after raw attribute assignment
        self.config.setdefault("threshold", 0.5)

    @staticmethod
    def upgrade(payload: dict, frm: int, to: int) -> dict:
        d = dict(payload)
        v = frm
        while v < to:
            if v == 1:  # example migration 1 -> 2
                d.setdefault("config", {}).setdefault("threshold", 0.5)
                # maybe rename "weights" -> "W" here, etc.
            v += 1
        return d



thing = MyThing()
dump_state(thing, "state.json")        # serialize full object
loaded_thing = load_state("state.json")  # -> MyThing instance
