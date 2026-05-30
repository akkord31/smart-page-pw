from __future__ import annotations
import threading
from typing import Any

from core.protocols import HookContext

# Импорт allure опциональный - плагин деградирует без него
try:
	import allure

	_ALLURE_AVAILABLE = True
except ImportError:
	_ALLURE_AVAILABLE = False

# Методы, которые не нужно показывать в отчёте - шум
_SKIP_TRACING = frozenset({
	"wait_for_timeout", "evaluate", "evaluate_handle",
	"wait_for_load_state", "wait_for_selector",
})

_THREAD_LOCAL = threading.local()


def _get_depth() -> int:
	return getattr(_THREAD_LOCAL, "depth", 0)


def _set_depth(v: int) -> None:
	_THREAD_LOCAL.depth = v


def _format_args(args: tuple, kwargs: dict) -> str:
	"""Компактное представление аргументов для step title."""
	parts = [repr(a) if len(repr(a)) < 80 else type(a).__name__ for a in args]
	parts += [f"{k}={repr(v)!s:.40}" for k, v in kwargs.items()]
	return ", ".join(parts)


class StepTracerPlugin:
	"""Инжектирует Allure steps для каждого Page action.

	Строит дерево шагов через depth counter:
	- Первый вызов на текущем уровне - создаёт Allure step
	- Вложенные вызовы (если SmartPage вызывает методы внутри хука) - дочерние шаги

	Без Allure деградирует до no-op.
	"""

	name = "step_tracer"
	priority = 2  # сразу после logger, до всего остального

	def __init__(
			self,
			include_args: bool = True,
			skip_methods: frozenset[str] = _SKIP_TRACING,
	) -> None:
		self._include_args = include_args
		self._skip = skip_methods
		# Храним стек контекстных менеджеров per-thread
		# (нужно для правильного __exit__ в on_after/on_error)
		_THREAD_LOCAL.step_stack = []

	def on_before(self, ctx: HookContext) -> None:
		if not _ALLURE_AVAILABLE or ctx.method in self._skip:
			return

		title = self._build_title(ctx)
		depth = _get_depth()
		_set_depth(depth + 1)

		step_ctx = allure.step(title)
		step_ctx.__enter__()

		stack = getattr(_THREAD_LOCAL, "step_stack", [])
		stack.append(step_ctx)
		_THREAD_LOCAL.step_stack = stack

		ctx.meta["_step_tracer_pushed"] = True

	def on_after(self, ctx: HookContext) -> None:
		if not ctx.meta.get("_step_tracer_pushed"):
			return
		self._pop_step(exc=None)

	def on_error(self, ctx: HookContext) -> None:
		if not ctx.meta.get("_step_tracer_pushed"):
			return
		self._pop_step(exc=ctx.exception)

	def _pop_step(self, exc: BaseException | None) -> None:
		stack = getattr(_THREAD_LOCAL, "step_stack", [])
		if not stack:
			return

		step_ctx = stack.pop()
		_set_depth(max(0, _get_depth() - 1))

		if exc is not None:
			step_ctx.__exit__(type(exc), exc, exc.__traceback__)
		else:
			step_ctx.__exit__(None, None, None)

	def _build_title(self, ctx: HookContext) -> str:
		method = ctx.method.replace("_", " ")
		if self._include_args and ctx.args:
			args_str = _format_args(ctx.args, ctx.kwargs)
			return f"{method}: {args_str}"
		return method
