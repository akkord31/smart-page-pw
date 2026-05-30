from __future__ import annotations
import time
import logging
from typing import Callable
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from core.protocols import HookContext

logger = logging.getLogger("smart_playwright.retry")

# Ошибки, при которых retry имеет смысл
_RETRYABLE = (PlaywrightTimeoutError, AssertionError)

# Методы, которые нельзя ретраить - side effects (клики, инпуты)
# ретраим только wait/assert-подобные операции
_NON_RETRYABLE_METHODS = frozenset({
	"fill", "type", "press", "select_option",
	"check", "uncheck", "upload_file",
})


class RetryPlugin:
	"""Повторяет упавший вызов с exponential backoff.

	Логика работы:
		on_error вызывается ПОСЛЕ первой попытки.
		Плагин делает (attempts - 1) повторных вызовов напрямую через _original_fn.
		Если все попытки исчерпаны - оставляет ctx.exception нетронутым,
		тогда SmartPage поднимает его наверх.
	"""

	name = "retry"
	priority = 10  # раньше screenshot, чтобы скрин был только после всех попыток

	def __init__(
			self,
			attempts: int = 3,
			delay_ms: float = 0.5,
			backoff: float = 2.0,
			retryable_methods: frozenset[str] | None = None,
	) -> None:
		if attempts < 1:
			raise ValueError("attempts must be >= 1")
		self._attempts = attempts
		self._delay_ms = delay_ms
		self._backoff = backoff
		# None означает "ретраить всё кроме _NON_RETRYABLE_METHODS"
		self._whitelist = retryable_methods

	def on_before(self, ctx: HookContext) -> None:
		...

	def on_after(self, ctx: HookContext) -> None:
		...

	def on_error(self, ctx: HookContext) -> None:
		if not self._should_retry(ctx):
			return  # оставляем ctx.exception — тест упадёт

		fn: Callable | None = ctx.meta.get("_original_fn")
		if fn is None:
			logger.warning("RetryPlugin: _original_fn not found in ctx.meta, skipping retry")
			return

		delay_ms = self._delay_ms
		last_exc: BaseException = ctx.exception  # type: ignore[assignment]

		for attempt in range(1, self._attempts):  # первая попытка уже была
			time.sleep(delay_ms)
			delay_ms *= self._backoff
			logger.debug(
				"[retry] %s | attempt %d/%d | after %.2fs",
				ctx.method, attempt + 1, self._attempts, delay_ms,
			)
			try:
				ctx.result = fn(*ctx.args, **ctx.kwargs)
				ctx.exception = None  # успех - снимаем исключение
				logger.debug("[retry] %s | succeeded on attempt %d", ctx.method, attempt + 1)
				return
			except Exception as e:  # noqa: BLE001
				last_exc = e
				logger.debug("[retry] %s | attempt %d failed: %s", ctx.method, attempt + 1, e)

		# Все попытки исчерпаны - пишем финальное исключение обратно
		ctx.exception = last_exc


	def _should_retry(self, ctx: HookContext) -> bool:
		if ctx.method in _NON_RETRYABLE_METHODS:
			return False
		if not isinstance(ctx.exception, _RETRYABLE):
			return False
		if self._whitelist is not None and ctx.method not in self._whitelist:
			return False
		return True
