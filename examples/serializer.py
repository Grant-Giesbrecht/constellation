# stateclass.py
from __future__ import annotations
from typing import Dict, Any, Sequence, Optional

def register_json_class(
	cls: Type,
	*,
	version: int | None = None,
	to: Optional[Callable[[Any], dict]] = None,
	from_: Optional[Callable[[dict], Any]] = None,
	upgrade: Optional[Callable[[dict, int, int], dict]] = None,
):
	if to is None:    to = getattr(cls, "to_jsonable")
	if from_ is None: from_ = getattr(cls, "from_jsonable")
	if version is None:
		version = getattr(cls, "__schema_version__", 1)
	if upgrade is None:
		upgrade = getattr(cls, "upgrade", None)

	# Idempotent registration (useful in hot-reload/test loops)
	prev = CLASS_REGISTRY.get(cls.__name__)
	if prev and prev.cls is cls and prev.version == version and prev.to == to and prev.from_ == from_ and prev.upgrade == upgrade:
		return cls

	CLASS_REGISTRY[cls.__name__] = ClassInfo(cls=cls, to=to, from_=from_, version=version, upgrade=upgrade)
	return cls

class SerializableMixin:
	"""
	Opt-in attribute-based serialization with auto-registration.

	Usage:
	  class MyThing(SerializableMixin):
		  __state_fields__ = ("when", "config", "foo", "bar")
		  __schema_version__ = 1
		  # optional:
		  # @staticmethod
		  # def upgrade(payload: dict, frm: int, to: int) -> dict: ...

	You can set __auto_register__ = False to opt out (e.g., abstract base).
	"""
	__state_fields__: Sequence[str] = ()
	__schema_version__: int = 1
	__auto_register__: bool = True  # opt-out switch for ABCs etc.

	def to_jsonable(self) -> Dict[str, Any]:
		return {name: getattr(self, name) for name in self.__state_fields__}

	@classmethod
	def from_jsonable(cls, data: Dict[str, Any]):
		obj = cls.__new__(cls)  # no __init__ call
		for k, v in data.items():
			setattr(obj, k, v)
		post = getattr(obj, "__post_deserialize__", None)
		if callable(post):
			post()
		return obj

	# Optional on subclasses:
	# @staticmethod
	# def upgrade(payload: dict, frm: int, to: int) -> dict: ...

	def __init_subclass__(cls, **kwargs):
		super().__init_subclass__(**kwargs)
		# Don’t auto-register the base mixin itself or opt-outs
		if cls is SerializableMixin:
			return
		if not getattr(cls, "__auto_register__", True):
			return
		# Basic sanity: ensure state fields are defined (helps catch typos)
		if not hasattr(cls, "__state_fields__"):
			raise AttributeError(f"{cls.__name__} must define __state_fields__")
		# Auto-register with defaults discovered from the class
		register_json_class(cls)
