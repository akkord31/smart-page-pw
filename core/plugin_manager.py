from __future__ import annotations
from contextlib import contextmanager
from typing import Iterator
from .protocols import Plugin, HookContext


class PluginManager:
    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    def register(self, plugin: Plugin) -> "PluginManager":
        if not isinstance(plugin, Plugin):
            raise TypeError(f"{plugin!r} does not satisfy Plugin protocol")
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: p.priority)
        return self

    def unregister(self, name: str) -> None:
        self._plugins = [p for p in self._plugins if p.name != name]

    @contextmanager
    def _plugin_scope(self, plugin: Plugin, ctx: HookContext) -> Iterator[None]:
        """Изолирует падение одного плагина от остальных"""
        try:
            yield
        except Exception as e:  # noqa: BLE001
            # Плагин не должен ронять тест - логируем и идём дальше
            import warnings
            warnings.warn(f"Plugin '{plugin.name}' raised in hook: {e}", stacklevel=3)

    def dispatch_before(self, ctx: HookContext) -> None:
        for plugin in self._plugins:
            with self._plugin_scope(plugin, ctx):
                plugin.on_before(ctx)
            if ctx.cancelled:
                break

    def dispatch_after(self, ctx: HookContext) -> None:
        for plugin in reversed(self._plugins):  # after - в обратном порядке
            with self._plugin_scope(plugin, ctx):
                plugin.on_after(ctx)

    def dispatch_error(self, ctx: HookContext) -> None:
        for plugin in reversed(self._plugins):
            with self._plugin_scope(plugin, ctx):
                plugin.on_error(ctx)
