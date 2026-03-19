"""Unit tests for MCP tools.

Tests the 15 tools registered via register_all():
- 8 core: screenshot, look, click, batch, help, type, key, shell
- 2 knowledge: add_knowledge, search_knowledge
- 5 dispatchers: chrome, window, find, file, manage
"""

import json
import base64
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from mcp.server.fastmcp import FastMCP, Image


def _make_jpeg(w=100, h=100):
    """Create a minimal valid JPEG for mocking screenshot()."""
    from PIL import Image as PILImage
    import io
    img = PILImage.new("RGB", (w, h), (128, 128, 128))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
def mcp_env(tmp_path):
    """Set up MCP environment with mocked desktop and manager."""
    mcp = FastMCP("test")
    mock_desktop = MagicMock()
    mock_desktop._grid_cols = None
    mock_desktop._grid_rows = None
    mock_manager = MagicMock()
    mock_manager._grid_state = {}
    log_calls = []

    def get_desktop(desktop_id):
        return mock_desktop

    def get_manager():
        return mock_manager

    def log_action(desktop_id, tool, params, result, t0, **kw):
        log_calls.append({"tool": tool, "params": params})

    from screenbox.tools import register_all
    register_all(mcp, get_desktop, get_manager, log_action)

    return {
        "mcp": mcp,
        "desktop": mock_desktop,
        "manager": mock_manager,
        "log_calls": log_calls,
        "tools": {t.name: t.fn for t in mcp._tool_manager._tools.values()},
    }


class TestDesktopHelp:
    def test_returns_strategy(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_help"]())
        assert "strategy" in result
        assert "priority" in result
        assert "anti_patterns" in result
        assert len(result["priority"]) >= 4

    def test_batch_mentioned(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_help"]())
        assert "batch" in result


class TestDesktopType:
    def test_basic_type(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_type"]("d1", "hello"))
        assert result["typed"] is True
        assert result["length"] == 5
        mcp_env["desktop"].type_text.assert_called_once_with("hello", 0)

    def test_type_with_delay(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_type"]("d1", "ab", delay=50))
        mcp_env["desktop"].type_text.assert_called_once_with("ab", 50)


class TestDesktopKey:
    def test_simple_key(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_key"]("d1", "Return"))
        assert result["pressed"] is True
        assert result["keys"] == "Return"
        mcp_env["desktop"].key.assert_called_once_with("Return")

    def test_ctrl_c_returns_clipboard(self, mcp_env):
        mcp_env["desktop"].get_clipboard.return_value = "copied"
        result = json.loads(mcp_env["tools"]["desktop_key"]("d1", "ctrl+c"))
        assert result["clipboard"] == "copied"

    def test_ctrl_x_returns_clipboard(self, mcp_env):
        mcp_env["desktop"].get_clipboard.return_value = "cut"
        result = json.loads(mcp_env["tools"]["desktop_key"]("d1", "ctrl+x"))
        assert result["clipboard"] == "cut"

    def test_ctrl_v_with_text_sets_clipboard(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_key"]("d1", "ctrl+v", text="pasted"))
        assert result["pasted"] is True
        assert result["length"] == 6
        mcp_env["desktop"].set_clipboard.assert_called_once_with("pasted")
        mcp_env["desktop"].key.assert_called_once_with("ctrl+v")

    def test_clipboard_error_handled(self, mcp_env):
        mcp_env["desktop"].get_clipboard.side_effect = Exception("fail")
        result = json.loads(mcp_env["tools"]["desktop_key"]("d1", "ctrl+c"))
        assert result["pressed"] is True
        assert result["clipboard"] is None


class TestDesktopShell:
    def test_basic_command(self, mcp_env):
        mcp_env["desktop"].shell.return_value = {
            "exit_code": 0, "stdout": "ok\n", "stderr": ""
        }
        result = json.loads(mcp_env["tools"]["desktop_shell"]("d1", "echo ok"))
        assert result["exit_code"] == 0
        mcp_env["desktop"].shell.assert_called_once_with("echo ok", 30)

    def test_custom_timeout(self, mcp_env):
        mcp_env["desktop"].shell.return_value = {"exit_code": 0, "stdout": "", "stderr": ""}
        mcp_env["tools"]["desktop_shell"]("d1", "sleep 10", timeout=60)
        mcp_env["desktop"].shell.assert_called_once_with("sleep 10", 60)


class TestDesktopClick:
    def test_single_click(self, mcp_env):
        mcp_env["desktop"]._adaptive_radius.return_value = 200
        mcp_env["desktop"]._cursor_ocr.return_value = {
            "elements": [], "count": 0
        }
        result_list = mcp_env["tools"]["desktop_click"]("d1", 100, 200)
        mcp_env["desktop"].click.assert_called_once_with(100, 200, 1)

    def test_double_click(self, mcp_env):
        mcp_env["desktop"]._adaptive_radius.return_value = 200
        mcp_env["desktop"]._cursor_ocr.return_value = {
            "elements": [], "count": 0
        }
        result_list = mcp_env["tools"]["desktop_click"]("d1", 50, 60, clicks=2)
        mcp_env["desktop"].double_click.assert_called_once_with(50, 60)

    def test_right_click(self, mcp_env):
        mcp_env["desktop"]._adaptive_radius.return_value = 200
        mcp_env["desktop"]._cursor_ocr.return_value = {
            "elements": [], "count": 0
        }
        mcp_env["tools"]["desktop_click"]("d1", 50, 60, button=3)
        mcp_env["desktop"].click.assert_called_once_with(50, 60, 3)

    def test_observe_returns_list(self, mcp_env):
        mcp_env["desktop"]._adaptive_radius.return_value = 200
        mcp_env["desktop"]._cursor_ocr.return_value = {
            "elements": [{"text": "OK", "cx": 100, "cy": 200, "conf": 90}],
            "count": 1,
            "image_base64": base64.b64encode(b"\xff\xd8fake").decode(),
        }
        result = mcp_env["tools"]["desktop_click"]("d1", 100, 200)
        assert isinstance(result, list)
        assert len(result) == 2  # Image + JSON

    def test_observe_false(self, mcp_env):
        result = mcp_env["tools"]["desktop_click"]("d1", 100, 200, observe=False)
        assert isinstance(result, list)
        parsed = json.loads(result[0])
        assert parsed["clicked"] is True
        # Should NOT call _cursor_ocr
        mcp_env["desktop"]._cursor_ocr.assert_not_called()

    def test_observe_error_handled(self, mcp_env):
        mcp_env["desktop"]._adaptive_radius.side_effect = Exception("ocr failed")
        result = mcp_env["tools"]["desktop_click"]("d1", 100, 200)
        assert isinstance(result, list)
        parsed = json.loads(result[0])
        assert parsed["clicked"] is True
        assert "observe_error" in parsed


class TestDesktopLook:
    def test_look_at_cell(self, mcp_env):
        mcp_env["desktop"].look_at_cell.return_value = {
            "elements": [{"text": "File", "cx": 50, "cy": 10, "conf": 95}],
            "image_base64": base64.b64encode(b"\xff\xd8fake").decode(),
            "image_size": {"w": 400, "h": 400},
        }
        mcp_env["desktop"]._xdotool.return_value = "Test Window"
        result = mcp_env["tools"]["desktop_look"]("d1", cell=3)
        assert isinstance(result, list)
        mcp_env["desktop"].look_at_cell.assert_called_once_with(3, "center", 200, 0, grid=None)

    def test_look_at_cursor(self, mcp_env):
        mcp_env["desktop"].look_at_cursor.return_value = {
            "elements": [],
            "image_base64": base64.b64encode(b"\xff\xd8fake").decode(),
            "image_size": {"w": 400, "h": 400},
        }
        mcp_env["desktop"]._xdotool.return_value = ""
        result = mcp_env["tools"]["desktop_look"]("d1", cell=0)
        mcp_env["desktop"].look_at_cursor.assert_called_once()

    def test_look_at_caret(self, mcp_env):
        mcp_env["desktop"].look_at_caret.return_value = {
            "elements": [],
        }
        mcp_env["desktop"]._xdotool.return_value = ""
        mcp_env["tools"]["desktop_look"]("d1", source="caret")
        mcp_env["desktop"].look_at_caret.assert_called_once()

    def test_click_tips_generated(self, mcp_env):
        mcp_env["desktop"].look_at_cursor.return_value = {
            "elements": [
                {"text": "Save", "cx": 100, "cy": 30, "conf": 92},
                {"text": "Cancel", "cx": 200, "cy": 30, "conf": 88},
            ],
        }
        mcp_env["desktop"]._xdotool.return_value = ""
        result = mcp_env["tools"]["desktop_look"]("d1")
        # Last element is JSON string
        parsed = json.loads(result[-1])
        assert "tip" in parsed
        assert "desktop_click" in parsed["tip"]


class TestDesktopBatch:
    def test_simple_click_sequence(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([
            {"action": "click", "x": 100, "y": 200},
            {"action": "key", "combo": "Return"},
        ])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_total"] == 2
        assert result["steps_done"] == 2
        mcp_env["desktop"]._exec.assert_called_once()

    def test_unknown_action(self, mcp_env):
        actions = json.dumps([{"action": "fly_to_moon"}])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert "error" in result

    def test_type_action(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([{"action": "type", "text": "hello"}])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_done"] == 1

    def test_sleep_action(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([{"action": "sleep", "ms": 100}])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_done"] == 1

    def test_scroll_action(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([
            {"action": "scroll", "direction": "down", "amount": 3}
        ])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_done"] == 1

    def test_drag_action(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([
            {"action": "drag", "x1": 10, "y1": 20, "x2": 100, "y2": 200}
        ])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_done"] == 1

    def test_modifier_click_action(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([
            {"action": "modifier_click", "x": 100, "y": 200, "modifiers": ["ctrl"]}
        ])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_done"] == 1


class TestDesktopScreenshot:
    def test_basic_screenshot(self, mcp_env):
        mcp_env["desktop"].screenshot.return_value = _make_jpeg()
        mcp_env["desktop"]._xdotool.return_value = ""
        mcp_env["desktop"].grid_on = MagicMock()
        result = mcp_env["tools"]["desktop_screenshot"]("d1")
        assert isinstance(result, (Image, list))

    def test_screenshot_with_knowledge(self, mcp_env):
        with patch("screenbox.tools.screenshot.get_store") as mock_store_fn:
            mcp_env["desktop"].screenshot.return_value = _make_jpeg()
            mcp_env["desktop"]._xdotool.return_value = "Sheet - LibreOffice Calc"
            mcp_env["desktop"].grid_on = MagicMock()
            store = MagicMock()
            store.get_hints_for_window.return_value = "[Knowledge: LibreOffice Calc]"
            mock_store_fn.return_value = store

            result = mcp_env["tools"]["desktop_screenshot"]("d1")
            assert isinstance(result, list)
            assert len(result) == 2
            assert isinstance(result[0], Image)
            assert "LibreOffice Calc" in result[1]

    def test_auto_grid(self, mcp_env):
        mcp_env["desktop"].screenshot.return_value = _make_jpeg()
        mcp_env["desktop"]._xdotool.return_value = ""
        mcp_env["desktop"].grid_on = MagicMock()
        result = mcp_env["tools"]["desktop_screenshot"]("d1")
        # Grid auto is handled internally now
        mcp_env["desktop"].screenshot.assert_called()


class TestDesktopAddKnowledge:
    def test_add_with_explicit_app(self, mcp_env):
        with patch("screenbox.tools.knowledge_tools.get_store") as mock_store_fn:
            store = MagicMock()
            store.add_fact.return_value = {"text": "Tip", "triggers": ["tag"]}
            store._load.return_value = {"facts": [{"text": "Tip"}]}
            mock_store_fn.return_value = store

            result = json.loads(mcp_env["tools"]["desktop_add_knowledge"](
                "d1", app="GIMP", fact="Use paths tool", triggers="paths,tool"
            ))
            assert result["saved"] is True
            store.add_fact.assert_called_once()

    def test_add_auto_detect_app(self, mcp_env):
        with patch("screenbox.tools.knowledge_tools.get_store") as mock_store_fn:
            store = MagicMock()
            store.add_fact.return_value = {"text": "Tip"}
            store._load.return_value = {"facts": [{"text": "Tip"}]}
            mock_store_fn.return_value = store
            mcp_env["desktop"]._xdotool.return_value = "File - LibreOffice Calc"

            result = json.loads(mcp_env["tools"]["desktop_add_knowledge"](
                "d1", fact="Alt+Y for yes", triggers="dialog,yes"
            ))
            assert result["saved"] is True
            # Should auto-detect libreoffice-calc
            call_args = store.add_fact.call_args
            assert call_args[0][0] == "libreoffice-calc"


class TestDesktopSearchKnowledge:
    def test_search_with_results(self, mcp_env):
        with patch("screenbox.tools.knowledge_tools.get_store") as mock_store_fn:
            store = MagicMock()
            store.search.return_value = [
                {"text": "Tip 1", "triggers": ["a"]},
                {"text": "Tip 2", "triggers": ["b"]},
            ]
            mock_store_fn.return_value = store
            mcp_env["desktop"]._xdotool.return_value = "GIMP"

            result = json.loads(mcp_env["tools"]["desktop_search_knowledge"](
                "d1", query="paths tool"
            ))
            assert len(result["results"]) == 2

    def test_search_no_app_detected(self, mcp_env):
        with patch("screenbox.tools.knowledge_tools.get_store") as mock_store_fn:
            mock_store_fn.return_value = MagicMock()
            mcp_env["desktop"]._xdotool.return_value = "desktop"

            result = json.loads(mcp_env["tools"]["desktop_search_knowledge"](
                "d1", query="something"
            ))
            assert "error" in result

    def test_search_no_results(self, mcp_env):
        with patch("screenbox.tools.knowledge_tools.get_store") as mock_store_fn:
            store = MagicMock()
            store.search.return_value = []
            mock_store_fn.return_value = store
            mcp_env["desktop"]._xdotool.return_value = "GIMP"

            result = json.loads(mcp_env["tools"]["desktop_search_knowledge"](
                "d1", query="nonexistent"
            ))
            assert result["results"] == []


class TestDesktopWindow:
    def test_list_windows(self, mcp_env):
        mcp_env["desktop"].get_windows.return_value = [
            {"id": "1", "name": "Chrome"}
        ]
        result = json.loads(mcp_env["tools"]["desktop_window"]("d1", "list"))
        assert len(result) == 1

    def test_activate(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_window"](
            "d1", "activate", window_id="123"
        ))
        assert result["activated"] is True
        mcp_env["desktop"].activate_window.assert_called_once_with("123")

    def test_minimize(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_window"](
            "d1", "minimize", window_id="123"
        ))
        assert result["minimized"] is True

    def test_maximize(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_window"]("d1", "maximize"))
        assert result["maximized"] is True

    def test_close(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_window"](
            "d1", "close", window_id="123"
        ))
        assert result["closed"] is True

    def test_resize(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_window"](
            "d1", "resize", width=800, height=600
        ))
        assert result["resized"] is True
        assert result["width"] == 800

    def test_move(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_window"](
            "d1", "move", x=100, y=50
        ))
        assert result["moved"] is True

    def test_unknown_action(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_window"]("d1", "fly"))
        assert "error" in result
        assert "available" in result


class TestDesktopFile:
    def test_upload(self, mcp_env):
        mcp_env["manager"].file_upload.return_value = True
        content = base64.b64encode(b"hello").decode()
        result = json.loads(mcp_env["tools"]["desktop_file"](
            "d1", "upload", path="/home/screenbox/file.txt",
            content_base64=content
        ))
        assert result["uploaded"] is True
        assert result["size"] == 5

    def test_upload_invalid_base64(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_file"](
            "d1", "upload", path="/tmp/f", content_base64="not-base64!!!"
        ))
        assert "error" in result

    def test_download(self, mcp_env):
        mcp_env["manager"].file_download.return_value = b"content"
        result = json.loads(mcp_env["tools"]["desktop_file"](
            "d1", "download", path="/home/screenbox/file.txt"
        ))
        assert result["size"] == 7
        assert "content_base64" in result

    def test_download_not_found(self, mcp_env):
        mcp_env["manager"].file_download.return_value = None
        result = json.loads(mcp_env["tools"]["desktop_file"](
            "d1", "download", path="/nonexistent"
        ))
        assert "error" in result

    def test_list(self, mcp_env):
        mcp_env["desktop"].shell.return_value = {"stdout": "total 0\n"}
        result = json.loads(mcp_env["tools"]["desktop_file"]("d1", "list"))
        assert "output" in result

    def test_upload_tar(self, mcp_env):
        mcp_env["manager"].file_upload_tar.return_value = True
        content = base64.b64encode(b"tardata").decode()
        result = json.loads(mcp_env["tools"]["desktop_file"](
            "d1", "upload_tar", content_base64=content
        ))
        assert result["extracted"] is True

    def test_unknown_action(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_file"]("d1", "delete"))
        assert "error" in result


class TestDesktopChrome:
    def test_navigate(self, mcp_env):
        mcp_env["desktop"].browser.navigate.return_value = {"success": True}
        result = json.loads(mcp_env["tools"]["desktop_chrome"](
            "d1", "navigate", url="https://example.com"
        ))
        assert result["success"] is True

    def test_page_map(self, mcp_env):
        mcp_env["desktop"].browser.page_map.return_value = {"headings": []}
        result = json.loads(mcp_env["tools"]["desktop_chrome"]("d1", "page_map"))
        assert "headings" in result

    def test_eval(self, mcp_env):
        mcp_env["desktop"].browser.execute_js.return_value = {"result": 42}
        result = json.loads(mcp_env["tools"]["desktop_chrome"](
            "d1", "eval", script="1+1"
        ))
        assert result["result"] == 42

    def test_tabs(self, mcp_env):
        mcp_env["desktop"].browser.get_tabs.return_value = [{"id": 1}]
        result = json.loads(mcp_env["tools"]["desktop_chrome"]("d1", "tabs"))
        assert len(result) == 1

    def test_unknown_action(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_chrome"]("d1", "teleport"))
        assert "error" in result
        assert "available" in result

    def test_screenshot_returns_image(self, mcp_env):
        mcp_env["desktop"].browser.browser_screenshot.return_value = b"\xff\xd8img"
        result = mcp_env["tools"]["desktop_chrome"]("d1", "screenshot")
        assert isinstance(result, Image)

    def test_screenshot_failure(self, mcp_env):
        mcp_env["desktop"].browser.browser_screenshot.return_value = None
        result = json.loads(mcp_env["tools"]["desktop_chrome"]("d1", "screenshot"))
        assert "error" in result


class TestDesktopDebug:
    def test_text_search(self, mcp_env):
        mcp_env["desktop"].find_on_screen.return_value = {
            "found": True, "matches": [{"text": "OK"}]
        }
        result = json.loads(mcp_env["tools"]["desktop_debug"](
            "d1", "text", text="OK"
        ))
        assert result["found"] is True

    def test_click_text(self, mcp_env):
        mcp_env["desktop"].click_on_text.return_value = {"clicked": True}
        result = json.loads(mcp_env["tools"]["desktop_debug"](
            "d1", "click_text", text="Save"
        ))
        assert result["clicked"] is True

    def test_a11y_apps(self, mcp_env):
        mcp_env["desktop"].accessibility_apps.return_value = ["Chrome", "Terminal"]
        result = json.loads(mcp_env["tools"]["desktop_debug"]("d1", "a11y_apps"))
        assert len(result) == 2

    def test_unknown_action(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_debug"]("d1", "laser_scan"))
        assert "error" in result


class TestDesktopManage:
    def test_destroy(self, mcp_env):
        mcp_env["manager"].destroy.return_value = True
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "destroy", desktop_id="d1", confirm=True
        ))
        assert result["destroyed"] is True

    def test_pause(self, mcp_env):
        mcp_env["manager"].pause.return_value = True
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "pause", desktop_id="d1"
        ))
        assert result["paused"] is True

    def test_resume(self, mcp_env):
        mcp_env["manager"].resume.return_value = True
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "resume", desktop_id="d1"
        ))
        assert result["resumed"] is True

    def test_snapshot_save(self, mcp_env):
        mcp_env["manager"].snapshot.return_value = "snap-001.tar.gz"
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "snapshot_save", desktop_id="d1", label="test"
        ))
        assert result["saved"] is True
        assert "snap-001" in result["filename"]

    def test_snapshot_restore(self, mcp_env):
        mcp_env["manager"].restore.return_value = True
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "snapshot_restore", desktop_id="d1"
        ))
        assert result["restored"] is True

    def test_clipboard_get(self, mcp_env):
        mcp_env["desktop"].get_clipboard.return_value = "text"
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "clipboard_get", desktop_id="d1"
        ))
        assert result["clipboard"] == "text"

    def test_clipboard_set(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "clipboard_set", desktop_id="d1", text="hello"
        ))
        assert result["set"] is True

    def test_grid_on(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "grid_on", desktop_id="d1", cell_size=150
        ))
        assert result["grid"] == "on"
        mcp_env["desktop"].grid_on.assert_called_once_with(cell_size=150)

    def test_grid_off(self, mcp_env):
        result = json.loads(mcp_env["tools"]["desktop_manage"](
            "grid_off", desktop_id="d1"
        ))
        assert result["grid"] == "off"


class TestBatchHelpers:
    """Test _action_to_bash and _shquote helper functions."""

    def test_action_to_bash_click(self, mcp_env):
        # Helpers are closures inside register(), test indirectly through batch
        # Test indirectly through batch
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([{"action": "click", "x": 100, "y": 200, "button": 1}])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_done"] == 1
        # Verify the bash script contains xdotool mousemove
        call_args = mcp_env["desktop"]._exec.call_args[0][0]
        script = call_args[-1]  # last arg is the bash script
        assert "xdotool mousemove 100 200" in script

    def test_action_to_bash_move(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        actions = json.dumps([{"action": "move", "x": 300, "y": 400}])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert result["steps_done"] == 1
        script = mcp_env["desktop"]._exec.call_args[0][0][-1]
        assert "xdotool mousemove 300 400" in script

    def test_invalid_key_combo_rejected(self, mcp_env):
        mcp_env["desktop"]._exec.return_value = ""
        # Key combo with invalid chars should produce None from _action_to_bash
        actions = json.dumps([{"action": "key", "combo": "ctrl+c; rm -rf /"}])
        result = json.loads(mcp_env["tools"]["desktop_batch"]("d1", actions))
        assert "error" in result
