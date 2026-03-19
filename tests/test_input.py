"""Unit tests for input mixin (desktop/input.py)."""

import json
import pytest
from unittest.mock import MagicMock


class MockInputDesktop:
    """Minimal mock that satisfies InputMixin dependencies."""

    def __init__(self):
        self.desktop_id = "test-1"
        self.manager = MagicMock()
        self._exec_calls = []
        self._xdotool_calls = []
        self._cursor_x = 500
        self._cursor_y = 300

    def _exec(self, cmd, timeout=10, user="screenbox"):
        self._exec_calls.append(cmd)
        if any("xdpyinfo" in str(c) for c in cmd):
            return "  dimensions:    1920x1080 pixels\n"
        cmd_str = " ".join(str(c) for c in cmd)
        if "xclip" in cmd_str and "-o" in cmd_str:
            return "clipboard content"
        return ""

    def _xdotool(self, *args):
        self._xdotool_calls.append(args)
        if "getactivewindow" in args:
            return "12345678"
        return ""

    def _log(self, *args, **kwargs):
        pass

    def _ensure_focus(self):
        pass

    def screenshot(self, quality=80):
        from PIL import Image
        import io
        img = Image.new("RGB", (1920, 1080), (50, 50, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50)
        return buf.getvalue()

    def get_cursor_position(self):
        return {"X": self._cursor_x, "Y": self._cursor_y}

    def get_mouse_pos(self):
        return self._cursor_x, self._cursor_y

    def shell(self, cmd):
        return {"stdout": "", "exit_code": 0}


from screenbox.desktop.input import InputMixin


class InputDesktop(InputMixin, MockInputDesktop):
    pass


class TestClick:
    def test_click_uses_exec(self):
        d = InputDesktop()
        d.click(100, 200)
        calls_str = str(d._exec_calls)
        assert "xdotool" in calls_str
        assert "100" in calls_str
        assert "200" in calls_str

    def test_click_button_3(self):
        d = InputDesktop()
        d.click(100, 200, button=3)
        calls_str = str(d._exec_calls)
        assert "mousedown" in calls_str
        assert "3" in calls_str

    def test_double_click(self):
        d = InputDesktop()
        d.double_click(100, 200)
        assert any("mousemove" in str(c) for c in d._xdotool_calls)
        assert any("click" in str(c) for c in d._xdotool_calls)

    def test_right_click(self):
        d = InputDesktop()
        d.right_click(300, 400)
        calls_str = str(d._exec_calls)
        assert "300" in calls_str
        assert "400" in calls_str
        assert "3" in calls_str  # button 3


class TestKeyboard:
    def test_key_combo(self):
        d = InputDesktop()
        d.key("ctrl+c")
        assert ("key", "--clearmodifiers", "ctrl+c") in d._xdotool_calls

    def test_key_return(self):
        d = InputDesktop()
        d.key("Return")
        assert ("key", "--clearmodifiers", "Return") in d._xdotool_calls


class TestTypeText:
    def test_type_text_uses_exec(self):
        d = InputDesktop()
        d.type_text("hello")
        calls_str = str(d._exec_calls)
        assert "xdotool" in calls_str
        assert "type" in calls_str
        assert "hello" in calls_str

    def test_type_text_with_delay(self):
        d = InputDesktop()
        d.type_text("abc", delay=50)
        calls_str = str(d._exec_calls)
        assert "--delay" in calls_str
        assert "50" in calls_str


class TestScroll:
    def test_scroll_down(self):
        d = InputDesktop()
        d.scroll("down", 3)
        calls_str = str(d._exec_calls)
        assert "5" in calls_str  # button 5 = scroll down
        assert "--repeat" in calls_str

    def test_scroll_up(self):
        d = InputDesktop()
        d.scroll("up", 2)
        calls_str = str(d._exec_calls)
        assert "4" in calls_str  # button 4 = scroll up

    def test_scroll_capped_at_20(self):
        d = InputDesktop()
        d.scroll("down", 100)
        calls_str = str(d._exec_calls)
        assert "--repeat 20" in calls_str


class TestMouseMove:
    def test_mouse_move(self):
        d = InputDesktop()
        d.mouse_move(400, 500)
        assert ("mousemove", "400", "500") in d._xdotool_calls

    def test_mouse_down(self):
        d = InputDesktop()
        d.mouse_down(100, 200, button=2)
        assert ("mousemove", "100", "200") in d._xdotool_calls
        assert ("mousedown", "2") in d._xdotool_calls

    def test_mouse_up(self):
        d = InputDesktop()
        d.mouse_up(100, 200, button=1)
        assert ("mousemove", "100", "200") in d._xdotool_calls
        assert ("mouseup", "1") in d._xdotool_calls


class TestDrag:
    def test_drag(self):
        d = InputDesktop()
        d.drag(100, 200, 300, 400)
        assert ("mousemove", "100", "200") in d._xdotool_calls
        assert ("mousemove", "--sync", "300", "400") in d._xdotool_calls
        # mousedown and mouseup via _exec
        exec_str = str(d._exec_calls)
        assert "mousedown" in exec_str
        assert "mouseup" in exec_str


class TestClipboard:
    def test_get_clipboard(self):
        d = InputDesktop()
        result = d.get_clipboard()
        assert result == "clipboard content"

    def test_set_clipboard(self):
        d = InputDesktop()
        d.set_clipboard("test data")
        calls_str = str(d._exec_calls)
        assert "xclip" in calls_str
        assert "clipboard" in calls_str


class TestHover:
    def test_hover(self):
        d = InputDesktop()
        d.hover(200, 300)
        assert ("mousemove", "200", "300") in d._xdotool_calls


class TestScreenshotSquare:
    def test_returns_jpeg(self):
        d = InputDesktop()
        result = d.screenshot_square(size=500, quality=50)
        assert len(result) > 100
        assert result[:2] == b'\xff\xd8'

    def test_correct_size(self):
        from PIL import Image
        import io
        d = InputDesktop()
        result = d.screenshot_square(size=500)
        img = Image.open(io.BytesIO(result))
        assert img.size == (500, 500)
