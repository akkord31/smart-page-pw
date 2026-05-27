import logging
import time
from ..core.protocols import HookContext

logger = logging.getLogger("smart_playwright.actions")

_SKIP_LOGGING = frozenset({"wait_for_timeout", "evaluate"})


class ActionLoggerPlugin:
    name = "action_logger"
    priority = 1 

    def on_before(self, ctx: HookContext) -> None:
        if ctx.method in _SKIP_LOGGING:
            return
        ctx.meta["_start_ts"] = time.monotonic()
        logger.debug("[→] %s | args=%s kwargs=%s", ctx.method, ctx.args, ctx.kwargs)

    def on_after(self, ctx: HookContext) -> None:
        if ctx.method in _SKIP_LOGGING:
            return
        elapsed = time.monotonic() - ctx.meta.get("_start_ts", time.monotonic())
        logger.debug("[✓] %s | %.3fs", ctx.method, elapsed)

    def on_error(self, ctx: HookContext) -> None:
        elapsed = time.monotonic() - ctx.meta.get("_start_ts", time.monotonic())
        logger.error("[✗] %s | %.3fs | %s", ctx.method, elapsed, ctx.exception)
