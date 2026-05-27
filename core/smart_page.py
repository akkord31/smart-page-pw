from __future__ import annotations
from typing import Any, Callable
from playwright.sync_api import Page
from .plugin_manager import PluginManager
from .protocols import HookContext

# Методы Page, которые не оборачиваем — служебные, не "действия"
_PASSTHROUGH = frozenset({"__class__", "__repr__", "context", "video", "workers"})

# Методы навигации — хотим знать о них отдельно
_NAVIGATION_METHODS = frozenset({"goto", "reload", "go_back", "go_forward"})


class SmartPage:
    """Transparent proxy над playwright.Page с plugin hook system."""

    def __init__(self, page: Page, plugins: PluginManager | None = None) -> None:
        # Используем object.__setattr__, чтобы не попасть в рекурсию через __setattr__
        object.__setattr__(self, "_page", page)
        object.__setattr__(self, "_pm", plugins or PluginManager())
        object.__setattr__(self, "_cache", {})

    # --- Proxy core ---

    def __getattr__(self, name: str) -> Any:
        if name in _PASSTHROUGH:
            return getattr(self._page, name)

        attr = getattr(self._page, name)

        if not callable(attr):
            return attr

        # Кешируем обёртки, не создаём closure на каждый __getattr__
        cache = object.__getattribute__(self, "_cache")
        if name not in cache:
            cache[name] = self._make_wrapper(name, attr)
        return cache[name]

    def _make_wrapper(self, method_name: str, fn: Callable) -> Callable:
        pm = object.__getattribute__(self, "_pm")

        def wrapper(*args: Any, **kwargs: Any) -> Any:
            ctx = HookContext(method=method_name, args=args, kwargs=kwargs)

            pm.dispatch_before(ctx)

            if ctx.cancelled:
                return ctx.result  # плагин подменил результат полностью

            try:
                # Если плагин уже проставил result (mock/stub) — не вызываем реальный метод
                if ctx.result is None:
                    ctx.result = fn(*ctx.args, **ctx.kwargs)
            except Exception as e:
                ctx.exception = e
                pm.dispatch_error(ctx)
                if ctx.exception is not None:
                    raise ctx.exception from None  # плагин мог подменить/подавить
            else:
                pm.dispatch_after(ctx)

            return ctx.result

        wrapper.__name__ = method_name
        return wrapper

    # --- Navigation shortcuts (typed, не через proxy) ---

    def goto(self, url: str, **kwargs: Any) -> Any:
        return self.__getattr__("goto")(url, **kwargs)

    # --- Plugin management API ---

    def use(self, plugin: Any) -> "SmartPage":
        """Fluent plugin registration: page.use(Logger()).use(Screenshot())"""
        pm = object.__getattribute__(self, "_pm")
        pm.register(plugin)
        return self

    @property
    def raw(self) -> Page:
        """Прямой доступ к оригинальному Page, если плагины мешают."""
        return object.__getattribute__(self, "_page")