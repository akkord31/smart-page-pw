import pytest
from playwright.sync_api import Page
from core.smart_page import SmartPage
from plugins.screenshot import ScreenshotOnFailurePlugin
from plugins.action_logger import ActionLoggerPlugin
from plugins.retry import RetryPlugin
from plugins.step_tracer import StepTracerPlugin
from plugins.network_audit import NetworkAuditPlugin


@pytest.fixture
def network_audit() -> NetworkAuditPlugin:
    """Отдельная фикстура для assert на репорт в тесте."""
    return NetworkAuditPlugin(
        block_patterns=["google-analytics", "hotjar", "doubleclick"],
    )


@pytest.fixture
def smart_page(page: Page, network_audit: NetworkAuditPlugin) -> SmartPage:
    return (
        SmartPage(page)
        .use(ActionLoggerPlugin())
        .use(StepTracerPlugin())
        .use(RetryPlugin(attempts=3, delay=0.3))
        .use(network_audit)
        .use(ScreenshotOnFailurePlugin())
    )
