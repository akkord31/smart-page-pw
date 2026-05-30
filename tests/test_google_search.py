# tests/test_google_search.py

"""
Demo suite: показывает SmartPage + Plugin system в реальном сценарии.

Сценарий: поиск на google.com
- ActionLogger  → логирует каждое действие с таймингом
- AssertionTracer → перехватывает expect() и добавляет в отчёт
- ScreenshotOnFailure → скриншот при падении
- RetryPlugin → retry для flaky click/fill
- NetworkAudit → interceptor on_before, проверяет что нет левых хостов

Каждый тест изолированно демонстрирует одну грань системы.
"""

import pytest
import logging
from playwright.sync_api import expect

from core.smart_page import SmartPage
from plugins.action_logger import ActionLoggerPlugin
from plugins.screenshot import ScreenshotOnFailurePlugin
from plugins.retry import RetryPlugin
from plugins.network_audit import NetworkAuditPlugin
from plugins.step_tracer import StepTracerPlugin

logger = logging.getLogger(__name__)

GOOGLE_URL = "https://www.google.com"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sp(page):
    """
    Базовый SmartPage для всех тестов.
    Fluent .use() - демонстрирует builder-like API поверх proxy.
    """
    return (
        SmartPage(page)
        .use(ActionLoggerPlugin())           # priority=1, первым
        .use(ScreenshotOnFailurePlugin())    # priority=100, последним в on_error
    )


@pytest.fixture
def sp_with_retry(page):
    """
    SmartPage с RetryPlugin - для демонстрации on_error → retry loop.
    RetryPlugin перехватывает TimeoutError и повторяет оригинальный вызов.
    """
    return (
        SmartPage(page)
        .use(ActionLoggerPlugin())
        .use(RetryPlugin(attempts=3, delay_ms=500))  # priority=10
        .use(ScreenshotOnFailurePlugin())
    )


@pytest.fixture
def sp_with_audit(page):
    """
    SmartPage с NetworkAuditPlugin - interceptor на уровне on_before.
    Блокирует вызов goto() если URL не из whitelist.
    Демонстрирует ctx.cancelled = True.
    """
    audit = NetworkAuditPlugin(
        allowed_hosts={"www.google.com", "google.com"}
    )
    return (
        SmartPage(page)
        .use(ActionLoggerPlugin())
        .use(audit)
    )


# ---------------------------------------------------------------------------
# Test 1: Базовый proxy — поиск работает, все хуки срабатывают
# ---------------------------------------------------------------------------

class TestGoogleSearch:
    """Smoke: SmartPage ведёт себя как обычный Page, но логирует."""

    def test_search_returns_results(self, sp: SmartPage):
        """
        Чистый happy path.
        ActionLogger записывает: goto → fill → press → wait_for_selector
        Убеждаемся что proxy не меняет поведение Page.
        """
        sp.goto(GOOGLE_URL)
        sp.get_by_name("q").fill("playwright python framework")
        sp.get_by_name("q").press("Enter")

        # expect работает на raw локаторах — SmartPage не мешает
        expect(sp.locator("#search")).to_be_visible(timeout=10_000)

        results = sp.locator("h3").all()
        assert len(results) > 0, "Нет результатов поиска"

    def test_action_log_captures_all_calls(self, sp: SmartPage, caplog):
        """
        Проверяем что ActionLogger видит каждое действие.
        Демонстрирует: plugin получает HookContext с method name до вызова.
        """
        with caplog.at_level(logging.DEBUG, logger="smart_playwright.actions"):
            sp.goto(GOOGLE_URL)
            sp.get_by_name("q").fill("test query")

        logged_methods = [
            r.message for r in caplog.records
            if r.name == "smart_playwright.actions"
        ]

        # goto и fill должны присутствовать в логе
        assert any("goto" in m for m in logged_methods), "goto не залогирован"
        assert any("fill" in m for m in logged_methods), "fill не залогирован"

    def test_timing_metadata_in_context(self, sp: SmartPage):
        """
        ActionLogger пишет _start_ts в ctx.meta.
        Проверяем что meta передаётся между on_before и on_after одного плагина.
        Plugin-to-plugin communication через HookContext.meta.
        """
        # Инспектируем через кастомный spy-плагин прямо в тесте
        captured_metas = []

        class MetaSpyPlugin:
            name = "meta_spy"
            priority = 50

            def on_before(self, ctx): pass
            def on_after(self, ctx): captured_metas.append(dict(ctx.meta))
            def on_error(self, ctx): pass

        sp.use(MetaSpyPlugin())
        sp.goto(GOOGLE_URL)

        # ActionLogger должен был проставить _start_ts
        assert any("_start_ts" in m for m in captured_metas), \
            "ActionLogger не записал _start_ts в ctx.meta"


# ---------------------------------------------------------------------------
# Test 2: Screenshot on failure — on_error chain
# ---------------------------------------------------------------------------

class TestScreenshotOnFailure:
    """Демонстрирует on_error interceptor chain."""

    def test_screenshot_created_on_timeout(self, sp: SmartPage, tmp_path):
        """
        При падении fill на несуществующем элементе:
        1. on_error вызывается у всех плагинов в reversed порядке
        2. ScreenshotOnFailurePlugin делает скриншот
        3. path записывается в ctx.meta["screenshot_path"]
        4. Исходный exception re-raise'ится (ctx.exception не подавляется)
        """
        # Перенастраиваем output_dir в tmp_path
        sp_local = SmartPage(sp.raw).use(
            ActionLoggerPlugin()
        ).use(
            ScreenshotOnFailurePlugin(output_dir=str(tmp_path))
        )

        sp_local.goto(GOOGLE_URL)

        with pytest.raises(Exception):
            # Элемента нет → TimeoutError → on_error chain
            sp_local.locator("#nonexistent_element_xyz").fill("text", timeout=1000)

        screenshots = list(tmp_path.glob("failure_*.png"))
        assert len(screenshots) == 1, (
            f"Ожидался 1 скриншот при падении, найдено: {len(screenshots)}"
        )

    def test_no_screenshot_on_success(self, sp: SmartPage, tmp_path):
        """
        Happy path: on_error не вызывается → скриншотов нет.
        Проверяем что плагин не создаёт мусор при успешных действиях.
        """
        sp_local = SmartPage(sp.raw).use(
            ScreenshotOnFailurePlugin(output_dir=str(tmp_path))
        )
        sp_local.goto(GOOGLE_URL)
        expect(sp_local.locator("body")).to_be_visible()

        screenshots = list(tmp_path.glob("failure_*.png"))
        assert len(screenshots) == 0


# ---------------------------------------------------------------------------
# Test 3: NetworkAuditPlugin — ctx.cancelled в действии
# ---------------------------------------------------------------------------

class TestNetworkAuditInterceptor:
    """
    Демонстрирует interceptor (не просто observer):
    плагин может отменить вызов через ctx.cancelled = True.
    """

    def test_allowed_host_passes_through(self, sp_with_audit: SmartPage):
        """Google в whitelist → goto выполняется нормально."""
        sp_with_audit.goto(GOOGLE_URL)
        expect(sp_with_audit.locator("body")).to_be_visible()

    def test_blocked_host_raises(self, sp_with_audit: SmartPage):
        """
        Хост не в whitelist → NetworkAuditPlugin ставит ctx.cancelled=True
        и ctx.exception = SecurityError → SmartPage re-raise'ит.
        Демонстрирует: плагин не просто наблюдает, а меняет поток выполнения.
        """
        with pytest.raises(PermissionError, match="blocked by NetworkAuditPlugin"):
            sp_with_audit.goto("https://evil-tracker.example.com")

    def test_audit_does_not_affect_other_methods(self, sp_with_audit: SmartPage):
        """
        NetworkAuditPlugin мониторит только goto/route.
        fill, click - проходят без проверки.
        Демонстрирует: плагины должны быть точечными, не глобальными.
        """
        sp_with_audit.goto(GOOGLE_URL)
        # fill не блокируется аудитом
        sp_with_audit.get_by_name("q").fill("test")


# ---------------------------------------------------------------------------
# Test 4: RetryPlugin - on_error с re-invoke оригинального метода
# ---------------------------------------------------------------------------

class TestRetryPlugin:
    """
    Самый нетривиальный плагин: из on_error вызывает метод повторно.
    SmartPage передаёт fn в HookContext через meta["_original_fn"].
    """

    def test_retry_succeeds_after_transient_failure(
        self, sp_with_retry: SmartPage, mocker
    ):
        """
        Мокируем page.click так, чтобы первые 2 вызова падали,
        третий - успешен. RetryPlugin должен поглотить первые 2 ошибки.
        """
        call_count = 0
        original_click = sp_with_retry.raw.locator("body").click

        def flaky_click(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise TimeoutError(f"Flaky attempt {call_count}")
            return original_click(*args, **kwargs)

        mocker.patch.object(
            sp_with_retry.raw.locator("body"), "click",
            side_effect=flaky_click
        )

        sp_with_retry.goto(GOOGLE_URL)
        sp_with_retry.locator("body").click()  # должен пройти с 3-й попытки

        assert call_count == 3, f"Ожидалось 3 попытки, было: {call_count}"

    def test_retry_exhausted_raises_original_exception(
        self, sp_with_retry: SmartPage, mocker
    ):
        """
        Если все attempts исчерпаны — RetryPlugin не подавляет ошибку.
        ctx.exception остаётся → SmartPage делает raise.
        """
        mocker.patch.object(
            sp_with_retry.raw,
            "goto",
            side_effect=TimeoutError("always fails")
        )

        with pytest.raises(TimeoutError, match="always fails"):
            sp_with_retry.goto(GOOGLE_URL)


# ---------------------------------------------------------------------------
# Test 5: E2E — все плагины вместе, реальный сценарий
# ---------------------------------------------------------------------------

class TestFullSearchFlow:
    """
    Полный сценарий с максимальной конфигурацией.
    Показывает что плагины не конфликтуют между собой.
    """

    def test_search_with_all_plugins(self, page):
        """
        Все плагины вместе:
        Logger(p=1) → NetworkAudit(p=5) → Retry(p=10) → Screenshot(p=100)

        before:  Logger → NetworkAudit → Retry → Screenshot
        after:   Screenshot → Retry → NetworkAudit → Logger  (reversed)
        on_error: Screenshot → Retry → NetworkAudit → Logger (reversed)
        """
        sp = (
            SmartPage(page)
            .use(ActionLoggerPlugin())
            .use(NetworkAuditPlugin(allowed_hosts={"www.google.com", "google.com"}))
            .use(RetryPlugin(attempts=2, delay_ms=300))
            .use(ScreenshotOnFailurePlugin())
        )

        sp.goto(GOOGLE_URL)
        sp.get_by_name("q").fill("playwright smart page plugin system")
        sp.get_by_name("q").press("Enter")

        expect(sp.locator("#search")).to_be_visible(timeout=10_000)

        # Убеждаемся что SmartPage.raw всегда даёт доступ к нативному Page
        # когда плагины мешают (например audit)
        raw_title = sp.raw.title()
        assert "Google" in raw_title

    def test_inline_plugin_in_test(self, page):
        """
        Демонстрирует что Plugin — это Protocol, не ABC.
        Любой объект с нужными атрибутами — плагин.
        Inline-плагин прямо в тесте, без импортов.
        """
        actions_log = []

        class InlineTracer:
            name = "inline_tracer"
            priority = 1

            def on_before(self, ctx):
                actions_log.append(f"BEFORE:{ctx.method}")

            def on_after(self, ctx):
                actions_log.append(f"AFTER:{ctx.method}")

            def on_error(self, ctx):
                actions_log.append(f"ERROR:{ctx.method}:{type(ctx.exception).__name__}")

        sp = SmartPage(page).use(InlineTracer())
        sp.goto(GOOGLE_URL)

        assert "BEFORE:goto" in actions_log
        assert "AFTER:goto" in actions_log
        # before всегда раньше after для одного метода
        before_idx = actions_log.index("BEFORE:goto")
        after_idx = actions_log.index("AFTER:goto")
        assert before_idx < after_idx
