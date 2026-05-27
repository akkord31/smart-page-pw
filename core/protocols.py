from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class HookContext:
	"""Мutable DTO, прокидываемый через весь plugin chain

	Плагин может:
	- читать/менять args/kwargs до вызова
	- инжектить result (тогда реальный метод не вызывается)
	- проставить cancelled=True для полной отмены
	"""
	method: str
	args: tuple
	kwargs: dict
	result: Any = None
	exception: BaseException | None = None
	cancelled: bool = False
	meta: dict[str, Any] = field(default_factory=dict)  # plugin-to-plugin communication


@runtime_checkable
class Plugin(Protocol):
	name: str
	priority: int  # меньше = раньше в цепочке

	def on_before(self, ctx: HookContext) -> None: ...

	def on_after(self, ctx: HookContext) -> None: ...

	def on_error(self, ctx: HookContext) -> None: ...
