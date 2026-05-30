from pathlib import Path
from datetime import datetime

from core.protocols import HookContext


class ScreenshotOnFailurePlugin:
    name = "screenshot_on_failure"
    priority = 100

    def __init__(self, output_dir: str = "screenshots") -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(exist_ok=True)

    def on_before(self, ctx: HookContext) -> None: ...
    def on_after(self, ctx: HookContext) -> None: ...

    def on_error(self, ctx: HookContext) -> None:
        page = ctx.meta.get("page")  # SmartPage прокидывает себя в meta
        if page is None:
            return
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = self._dir / f"failure_{ctx.method}_{ts}.png"
        page.raw.screenshot(path=str(path), full_page=True)
        ctx.meta["screenshot_path"] = str(path)
