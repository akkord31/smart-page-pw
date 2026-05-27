import pytest
from playwright.sync_api import Page

from core.smart_page import SmartPage
from plugins.action_logger import ActionLoggerPlugin
from plugins.screenshot import ScreenshotOnFailurePlugin


@pytest.fixture
def smart_page(page: Page) -> SmartPage:
    return (
        SmartPage(page)
        .use(ActionLoggerPlugin())
        .use(ScreenshotOnFailurePlugin())
    )
