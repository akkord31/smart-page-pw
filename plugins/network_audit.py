from __future__ import annotations
import json
import time
import threading
import logging
from dataclasses import dataclass, field
from playwright.sync_api import Page, Request, Response, Route

from core.protocols import HookContext

logger = logging.getLogger("smart_playwright.network")


@dataclass
class RequestRecord:
	url: str
	method: str
	headers: dict[str, str]
	post_data: str | None
	timestamp: float
	response_status: int | None = None
	response_time_ms: float | None = None
	blocked: bool = False
	error: str | None = None


@dataclass
class NetworkAuditReport:
	records: list[RequestRecord] = field(default_factory=list)

	def by_url(self, pattern: str) -> list[RequestRecord]:
		return [r for r in self.records if pattern in r.url]

	def failed(self) -> list[RequestRecord]:
		return [r for r in self.records if r.error or (r.response_status and r.response_status >= 400)]

	def slow(self, threshold_ms: float = 1000.0) -> list[RequestRecord]:
		return [r for r in self.records if r.response_time_ms and r.response_time_ms > threshold_ms]

	def blocked_requests(self) -> list[RequestRecord]:
		return [r for r in self.records if r.blocked]

	def assert_no_failures(self) -> None:
		failed = self.failed()
		if failed:
			details = "\n".join(f"  {r.method} {r.url} → {r.response_status or r.error}" for r in failed)
			raise AssertionError(f"Network failures detected:\n{details}")

	def assert_no_slow_requests(self, threshold_ms: float = 1000.0) -> None:
		slow = self.slow(threshold_ms)
		if slow:
			details = "\n".join(f"  {r.url} → {r.response_time_ms:.0f}ms" for r in slow)
			raise AssertionError(f"Slow requests (>{threshold_ms}ms):\n{details}")


class NetworkAuditPlugin:
	"""Аудит сетевых запросов через Playwright event listeners.

	Архитектурная особенность:
		Не работает через HookContext (методы Page не отвечают за сеть напрямую).
		Подписывается на page.on("request") / page.on("response") при первом
		обращении к методу 'goto' или явном вызове attach()

	Дополнительно умеет:
		- Блокировать запросы по паттерну (аналитика, реклама)
		- Патчить заголовки на лету
		- Экспортировать HAR-подобный JSON
	"""

	name = "network_audit"
	priority = 5

	def __init__(
			self,
			block_patterns: list[str] | None = None,
			patch_headers: dict[str, str] | None = None,
	) -> None:
		self._block_patterns = block_patterns or []
		self._patch_headers = patch_headers or {}
		self._report = NetworkAuditReport()
		self._pending: dict[str, float] = {}  # url → start timestamp
		self._lock = threading.Lock()
		self._attached = False

	# --- Plugin Protocol ---

	def on_before(self, ctx: HookContext) -> None:
		"""Подписываемся на сетевые события при первом навигационном вызове."""
		if self._attached:
			return
		page: "SmartPage | None" = ctx.meta.get("page")
		if page is not None and ctx.method in {"goto", "reload", "go_back", "go_forward"}:
			self.attach(page.raw)

	def on_after(self, ctx: HookContext) -> None:
		...

	def on_error(self, ctx: HookContext) -> None:
		...

	# --- Public API ---

	def attach(self, page: Page) -> None:
		"""Явная подписка - вызвать до goto(), если нужна запись с самого начала."""
		if self._attached:
			return

		if self._block_patterns or self._patch_headers:
			page.route("**/*", self._handle_route)

		page.on("request", self._on_request)
		page.on("response", self._on_response)
		page.on("requestfailed", self._on_request_failed)

		self._attached = True
		logger.debug("NetworkAuditPlugin attached")

	@property
	def report(self) -> NetworkAuditReport:
		return self._report

	def export_json(self, path: str) -> None:
		import dataclasses
		with open(path, "w", encoding="utf-8") as f:
			json.dump(
				[dataclasses.asdict(r) for r in self._report.records],
				f,
				indent=2,
				default=str,
			)

	# --- Playwright event handlers ---

	def _handle_route(self, route: Route, request: Request) -> None:
		url = request.url

		# Блокировка по паттерну
		if any(p in url for p in self._block_patterns):
			logger.debug("[network] blocked: %s", url)
			with self._lock:
				# Записываем как заблокированный запрос
				record = RequestRecord(
					url=url,
					method=request.method,
					headers=dict(request.headers),
					post_data=request.post_data,
					timestamp=time.time(),
					blocked=True,
				)
				self._report.records.append(record)
			route.abort()
			return

		# Патчинг заголовков
		if self._patch_headers:
			headers = {**request.headers, **self._patch_headers}
			route.continue_(headers=headers)
			return

		route.continue_()

	def _on_request(self, request: Request) -> None:
		with self._lock:
			self._pending[request.url] = time.monotonic()

		record = RequestRecord(
			url=request.url,
			method=request.method,
			headers=dict(request.headers),
			post_data=request.post_data,
			timestamp=time.time(),
		)
		with self._lock:
			self._report.records.append(record)

		logger.debug("[→] %s %s", request.method, request.url)

	def _on_response(self, response: Response) -> None:
		with self._lock:
			start = self._pending.pop(response.url, None)

		elapsed_ms = (time.monotonic() - start) * 1000 if start else None

		# Обновляем последний record с этим URL
		with self._lock:
			for record in reversed(self._report.records):
				if record.url == response.url and record.response_status is None:
					record.response_status = response.status
					record.response_time_ms = elapsed_ms
					break

		logger.debug(
			"[←] %d %s (%.0fms)",
			response.status, response.url, elapsed_ms or 0,
		)

	def _on_request_failed(self, request: Request) -> None:
		failure = request.failure or "unknown error"
		with self._lock:
			for record in reversed(self._report.records):
				if record.url == request.url and record.error is None:
					record.error = failure
					break
		logger.error("[✗] request failed: %s | %s", request.url, failure)
