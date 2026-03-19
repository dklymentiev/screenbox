"""Unit tests for BrowserClient (browser.py)."""

import json
import pytest
from unittest.mock import MagicMock, patch

from screenbox.browser import BrowserClient


@pytest.fixture
def browser():
    """Create BrowserClient with mocked Desktop."""
    desktop = MagicMock()
    desktop.desktop_id = "test-1"
    desktop._last_ws_ms = 0

    def ext_cmd(command, params=None, timeout=10):
        """Mock extension commands returning realistic data."""
        if command == "navigate":
            return {"success": True, "url": params.get("url", "")}
        if command == "getTabs":
            return {"tabs": [{"id": 1, "url": "https://example.com", "title": "Example"}]}
        if command == "newTab":
            return {"id": 2, "url": params.get("url", "about:blank")}
        if command == "closeTab":
            return {"success": True}
        if command == "switchTab":
            return {"success": True}
        if command == "getSemantics":
            return {"u": "https://example.com", "t": "Example", "n": 5, "e": [
                {"i": 1, "t": "a", "l": "Link", "r": [10, 20, 100, 30]}
            ]}
        if command == "pageRead":
            return {"text": "Hello World", "chars": 11, "title": "Example"}
        if command == "viewRead":
            return {"text": "Visible text", "scroll": "down"}
        if command == "cursorRead":
            return {"text": "Element text"}
        if command == "executeJs":
            return {"result": params.get("script", "")}
        if command == "waitFor":
            return {"matched": True}
        if command == "search":
            return {"found": True, "count": 3}
        if command == "getCookies":
            return {"cookies": []}
        if command == "setCookies":
            return {"success": True}
        if command == "clearCookies":
            return {"success": True}
        if command == "sslErrors":
            return {"ignore": True}
        if command == "consoleStart":
            return {"started": True}
        if command == "consoleStop":
            return {"stopped": True}
        if command == "consoleGet":
            return {"entries": []}
        if command == "network":
            return {"requests": []}
        if command == "getPageInfo":
            return {"url": "https://example.com", "title": "Example"}
        if command == "domSnapshot":
            return {"html": "<html></html>"}
        if command == "click":
            return {"clicked": True}
        if command == "type":
            return {"typed": True}
        if command == "performance":
            return {"metrics": {}}
        if command == "emulate":
            return {"emulated": True}
        if command == "geolocation":
            return {"set": True}
        return {}

    desktop._ext_cmd = ext_cmd
    desktop._exec.return_value = ""
    desktop._exec_bytes.return_value = b""
    desktop._xdotool.return_value = ""
    desktop._log = MagicMock()

    return BrowserClient(desktop)


class TestNavigation:
    def test_navigate(self, browser):
        result = browser.navigate("https://example.com")
        assert result["success"] is True
        assert result["url"] == "https://example.com"

    def test_go_back(self, browser):
        browser._d._ext_cmd = MagicMock(return_value={"result": "ok"})
        result = browser.go_back()
        assert isinstance(result, dict)

    def test_go_forward(self, browser):
        browser._d._ext_cmd = MagicMock(return_value={"result": "ok"})
        result = browser.go_forward()
        assert isinstance(result, dict)

    def test_wait_for(self, browser):
        result = browser.wait_for("url:example.com", 5000)
        assert result["matched"] is True

    def test_wait_for_error(self, browser):
        browser._d._ext_cmd = MagicMock(side_effect=Exception("timeout"))
        result = browser.wait_for("url:example.com")
        assert "error" in result


class TestTabs:
    def test_get_tabs(self, browser):
        result = browser.get_tabs()
        assert "tabs" in result
        assert len(result["tabs"]) == 1

    def test_new_tab(self, browser):
        result = browser.new_tab("https://test.com")
        assert result["id"] == 2

    def test_new_tab_fallback(self, browser):
        browser._d._ext_cmd = MagicMock(side_effect=Exception("fail"))
        result = browser.new_tab()
        assert result["method"] == "xdotool"

    def test_close_tab_by_id(self, browser):
        result = browser.close_tab(tab_id=1)
        assert result["success"] is True

    def test_close_tab_fallback(self, browser):
        result = browser.close_tab()
        assert result["method"] == "xdotool"

    def test_switch_tab(self, browser):
        result = browser.switch_tab(1)
        assert result["success"] is True

    def test_switch_tab_error(self, browser):
        browser._d._ext_cmd = MagicMock(side_effect=Exception("fail"))
        result = browser.switch_tab(1)
        assert "error" in result


class TestContentPerception:
    def test_page_map(self, browser):
        result = browser.page_map()
        assert result["n"] == 5
        assert len(result["e"]) == 1

    def test_page_map_error(self, browser):
        browser._d._ext_cmd = MagicMock(side_effect=Exception("ws error"))
        result = browser.page_map()
        assert "error" in result

    def test_page_read(self, browser):
        # Mock the curl call for empty page guard
        browser._d._exec.return_value = json.dumps([{"url": "https://example.com"}])
        result = browser.page_read()
        assert result["text"] == "Hello World"

    def test_page_read_empty_page(self, browser):
        browser._d._exec.return_value = json.dumps([{"url": "about:blank"}])
        result = browser.page_read()
        assert result["chars"] == 0

    def test_view_read(self, browser):
        result = browser.view_read()
        assert result["text"] == "Visible text"

    def test_cursor_read(self, browser):
        # cursor_read uses extension command "cursorRead"
        # but also needs mouse pos for coordinate translation
        browser._d._ext_cmd = MagicMock(return_value={"text": "Element text"})
        result = browser.cursor_read()
        assert isinstance(result, dict)


class TestCookies:
    def test_get_cookies(self, browser):
        result = browser.get_cookies()
        assert "cookies" in result

    def test_set_cookies(self, browser):
        result = browser.set_cookies([{"name": "test", "value": "1"}])
        assert result["success"] is True

    def test_clear_cookies(self, browser):
        result = browser.clear_cookies()
        assert result["success"] is True


class TestDOMInteraction:
    def test_ext_click(self, browser):
        result = browser.ext_click("#button")
        assert isinstance(result, dict)

    def test_ext_type(self, browser):
        result = browser.ext_type("#input", "text")
        assert isinstance(result, dict)

    def test_chrome_search(self, browser):
        result = browser.chrome_search("test query")
        assert isinstance(result, dict)


class TestCDP:
    def test_execute_js(self, browser):
        result = browser.execute_js("1 + 1")
        assert isinstance(result, dict)

    def test_get_page_info(self, browser):
        result = browser.get_page_info()
        assert result["url"] == "https://example.com"

    def test_console_start(self, browser):
        # console_start uses _cdp_eval -> _cdp_command -> _exec_bytes
        browser._d._exec_bytes = MagicMock(return_value=json.dumps(
            {"result": {"value": "started"}}
        ).encode())
        result = browser.console_start()
        assert isinstance(result, dict)

    def test_console_stop(self, browser):
        browser._d._exec_bytes = MagicMock(return_value=json.dumps(
            {"result": {"value": "stopped"}}
        ).encode())
        result = browser.console_stop()
        assert isinstance(result, dict)

    def test_console_get(self, browser):
        browser._d._exec_bytes = MagicMock(return_value=json.dumps(
            {"result": {"value": []}}
        ).encode())
        result = browser.console_get()
        assert "logs" in result

    def test_network_get(self, browser):
        browser._d._exec_bytes = MagicMock(return_value=json.dumps(
            {"result": {"value": []}}
        ).encode())
        result = browser.network_get()
        assert "entries" in result

    def test_ignore_ssl_errors(self, browser):
        # ignore_ssl_errors uses _cdp_command directly
        browser._d._exec_bytes = MagicMock(return_value=json.dumps(
            {"result": {}}
        ).encode())
        result = browser.ignore_ssl_errors(True)
        assert result["ignore_ssl"] is True

    def test_performance_metrics(self, browser):
        browser._d._exec_bytes = MagicMock(return_value=json.dumps(
            {"result": {"metrics": []}}
        ).encode())
        result = browser.performance_metrics()
        assert isinstance(result, dict)


class TestHealth:
    def test_chrome_ready(self, browser):
        browser._d._exec.return_value = json.dumps([{"url": "https://example.com"}])
        result = browser.chrome_ready()
        assert isinstance(result, dict)

    def test_chrome_ready_not_started(self, browser):
        browser._d._exec.return_value = ""
        result = browser.chrome_ready()
        assert isinstance(result, dict)
