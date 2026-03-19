"""Unit tests for OCR mixin (desktop/ocr.py)."""

import io
import json
import pytest
from unittest.mock import MagicMock, patch
from PIL import Image
import numpy as np


def _make_image(w=1920, h=1080, color=(50, 50, 50)):
    """Create a test JPEG image."""
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


class MockOCRDesktop:
    """Minimal mock that satisfies OCRMixin dependencies."""

    def __init__(self):
        self.desktop_id = "test-1"
        self._cursor_x = 500
        self._cursor_y = 300
        self._exec_calls = []

    def screenshot(self, quality=80):
        return _make_image()

    def get_cursor_position(self):
        return {"X": self._cursor_x, "Y": self._cursor_y}

    def get_mouse_pos(self):
        return self._cursor_x, self._cursor_y

    def _exec(self, cmd, timeout=10, user="screenbox"):
        self._exec_calls.append(cmd)
        return ""

    def _atspi_exec(self, script, timeout=10):
        return json.dumps({"found": False})

    def _xdotool(self, *args):
        return ""

    def _adaptive_radius(self, ocr_radius):
        return ocr_radius if ocr_radius > 0 else 200

    def _cell_coords(self, cell, position, cell_size, grid=None):
        return {"cell": cell, "x": 960, "y": 540, "position": position,
                "cell_bounds": {"x1": 0, "y1": 0, "x2": 192, "y2": 180}}


from screenbox.desktop.ocr import OCRMixin


class OCRDesktop(OCRMixin, MockOCRDesktop):
    pass


# Mock pytesseract globally for all tests
@pytest.fixture(autouse=True)
def mock_tesseract():
    """Mock pytesseract to avoid requiring Tesseract binary."""
    empty_data = {
        "text": ["Hello", "World", ""],
        "conf": [90, 85, -1],
        "left": [10, 200, 0],
        "top": [20, 20, 0],
        "width": [80, 100, 0],
        "height": [25, 25, 0],
    }
    with patch("screenbox.desktop.ocr.pytesseract") as mock_pt:
        mock_pt.image_to_data.return_value = empty_data
        mock_pt.image_to_string.return_value = "Ok"
        mock_pt.Output = MagicMock()
        mock_pt.Output.DICT = "dict"
        yield mock_pt


class TestCursorOCR:
    def test_basic_ocr(self):
        d = OCRDesktop()
        coords = {"x": 500, "y": 300}
        result = d._cursor_ocr(coords, radius=200)
        assert "elements" in result
        assert "count" in result
        assert "radius" in result
        assert result["radius"] == 200

    def test_ocr_with_image(self):
        d = OCRDesktop()
        coords = {"x": 500, "y": 300}
        result = d._cursor_ocr(coords, radius=200, include_image=True)
        assert "image_base64" in result
        assert "image_size" in result

    def test_ocr_without_image(self):
        d = OCRDesktop()
        coords = {"x": 500, "y": 300}
        result = d._cursor_ocr(coords, radius=200, include_image=False)
        assert "image_base64" not in result

    def test_ocr_out_of_bounds_clamped(self):
        d = OCRDesktop()
        coords = {"x": -100, "y": -50}
        result = d._cursor_ocr(coords, radius=200)
        assert "warning" in result
        assert result["x"] == 0
        assert result["y"] == 0

    def test_ocr_no_screenshot(self):
        d = OCRDesktop()
        d.screenshot = MagicMock(return_value=b"")
        coords = {"x": 500, "y": 300}
        result = d._cursor_ocr(coords, radius=200)
        # Should still work (inspect_area handles empty screenshot)
        assert isinstance(result, dict)


class TestLookAtCursor:
    def test_basic(self):
        d = OCRDesktop()
        result = d.look_at_cursor()
        assert result["looked"] is True
        assert result["x"] == 500
        assert result["y"] == 300
        assert result["mode"] == "cursor"

    def test_custom_radius(self):
        d = OCRDesktop()
        result = d.look_at_cursor(ocr_radius=300)
        assert result["radius"] == 300


class TestGetCaretPosition:
    def test_caret_not_found(self):
        d = OCRDesktop()
        result = d.get_caret_position()
        assert result["found"] is False

    def test_caret_found(self):
        d = OCRDesktop()
        d._atspi_exec = MagicMock(return_value=json.dumps({
            "found": True, "x": 200, "y": 300,
            "w": 2, "h": 16, "offset": 5,
            "app": "gedit", "widget": "text", "role": "text"
        }))
        result = d.get_caret_position()
        assert result["found"] is True
        assert result["x"] == 200
        assert result["y"] == 300

    def test_caret_atspi_error(self):
        d = OCRDesktop()
        d._atspi_exec = MagicMock(side_effect=Exception("atspi error"))
        result = d.get_caret_position()
        assert result["found"] is False


class TestLookAtCaret:
    def test_fallback_to_cursor(self):
        d = OCRDesktop()
        result = d.look_at_caret()
        assert result["mode"] == "cursor"
        assert result.get("caret_fallback") is True

    def test_caret_mode(self):
        d = OCRDesktop()
        d._atspi_exec = MagicMock(return_value=json.dumps({
            "found": True, "x": 200, "y": 300,
            "offset": 5, "widget": "textview", "role": "text"
        }))
        result = d.look_at_caret()
        assert result["mode"] == "caret"
        assert result["x"] == 200
        assert result["y"] == 300


class TestInspectCell:
    def test_basic_cell(self):
        d = OCRDesktop()
        result = d.inspect_cell("1", cols=3, rows=3)
        assert "elements" in result
        assert "count" in result
        assert "viewport" in result
        assert "breadcrumbs" in result
        assert len(result["breadcrumbs"]) == 1

    def test_two_level_zoom(self):
        d = OCRDesktop()
        result = d.inspect_cell("2,5", cols=3, rows=3)
        assert len(result["breadcrumbs"]) == 2

    def test_empty_screenshot(self):
        d = OCRDesktop()
        d.screenshot = MagicMock(return_value=b"")
        result = d.inspect_cell("1")
        assert "error" in result

    def test_cell_coordinates_absolute(self):
        d = OCRDesktop()
        result = d.inspect_cell("5", cols=3, rows=3)
        # Cell 5 in 3x3 is center cell: row=1, col=1
        viewport = result["viewport"]
        assert viewport[0] == 640  # abs_x = col*cell_w = 1*640
        assert viewport[1] == 360  # abs_y = row*cell_h = 1*360


class TestObserve:
    def test_basic(self):
        d = OCRDesktop()
        result = d.observe(radius=150)
        assert "elements" in result
        assert "mouse" in result
        assert result["mouse"] == [500, 300]


class TestInspectArea:
    def test_basic(self):
        d = OCRDesktop()
        result = d.inspect_area(500, 300, radius=200)
        assert "elements" in result
        assert "count" in result
        assert "viewport" in result
        assert "center" in result
        assert result["center"] == [500, 300]
        assert result["radius"] == 200

    def test_zoom_clamped(self):
        d = OCRDesktop()
        result = d.inspect_area(500, 300, radius=200, zoom=10)
        assert result["zoom"] == 5  # clamped to max 5

    def test_edge_crop_padding(self):
        d = OCRDesktop()
        # Near top-left corner -- should still work with black padding
        result = d.inspect_area(10, 10, radius=200)
        assert result["center"] == [10, 10]
        # Viewport origin goes negative (shows black padding)
        assert result["viewport"][0] == -190
        assert result["viewport"][1] == -190

    def test_elements_have_screen_coords(self):
        d = OCRDesktop()
        result = d.inspect_area(500, 300, radius=200)
        for el in result["elements"]:
            assert "x" in el
            assert "y" in el
            assert "cx" in el
            assert "cy" in el
            assert "text" in el
            assert "conf" in el

    def test_overlapped_cells_computed(self):
        d = OCRDesktop()
        result = d.inspect_area(960, 540, radius=200)
        assert "overlapped_cells" in result
        assert len(result["overlapped_cells"]) > 0

    def test_empty_screenshot_error(self):
        d = OCRDesktop()
        d.screenshot = MagicMock(return_value=b"")
        result = d.inspect_area(500, 300)
        assert result == {"error": "Screenshot failed"}

    def test_dark_theme_inversion(self, mock_tesseract):
        """Dark theme (median < 128) should be inverted for OCR."""
        d = OCRDesktop()
        # Dark image
        d.screenshot = MagicMock(return_value=_make_image(color=(20, 20, 20)))
        result = d.inspect_area(500, 300, radius=200)
        assert "elements" in result
        # OCR should still be called (on inverted image)
        assert mock_tesseract.image_to_data.called

    def test_conf_threshold_filtering(self, mock_tesseract):
        """Elements below conf_threshold should be filtered out."""
        mock_tesseract.image_to_data.return_value = {
            "text": ["Good", "Bad", ""],
            "conf": [90, 10, -1],
            "left": [10, 200, 0],
            "top": [20, 20, 0],
            "width": [80, 100, 0],
            "height": [25, 25, 0],
        }
        d = OCRDesktop()
        result = d.inspect_area(500, 300, radius=200, conf_threshold=50)
        # Only "Good" (conf=90) should pass threshold of 50
        texts = [e["text"] for e in result["elements"]]
        assert "Good" in texts
        assert "Bad" not in texts

    def test_gaze_event_written(self):
        d = OCRDesktop()
        d.inspect_area(500, 300, radius=200)
        # Should write gaze event via _exec
        exec_str = str(d._exec_calls)
        assert "gaze-event" in exec_str
