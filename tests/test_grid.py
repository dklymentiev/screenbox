"""Unit tests for grid overlay and cell calculations (desktop/grid.py)."""

import pytest
from unittest.mock import MagicMock, patch


class MockGridDesktop:
    """Minimal mock that satisfies GridMixin dependencies."""

    def __init__(self):
        self.desktop_id = "test-1"
        self.manager = MagicMock()
        self.manager._grid_state = {}
        self._grid_cols = None
        self._grid_rows = None

    def _exec(self, cmd, timeout=10):
        # Return xdpyinfo-like output for _get_screen_dims
        return "  dimensions:    1920x1080 pixels\n"

    def _xdotool(self, *args):
        return ""

    def screenshot(self, quality=80):
        # Return minimal 10x10 JPEG
        from PIL import Image
        import io
        img = Image.new("RGB", (1920, 1080), (50, 50, 50))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=50)
        return buf.getvalue()

    def get_cursor_position(self):
        return {"X": 960, "Y": 540}

    def click(self, x, y, button=1):
        pass

    def _adaptive_radius(self, ocr_radius):
        return ocr_radius if ocr_radius > 0 else 200

    def _cursor_ocr(self, coords, radius=200):
        coords["elements"] = []
        coords["count"] = 0
        coords["radius"] = radius
        return coords


# Dynamically create a class that uses GridMixin
from screenbox.desktop.grid import GridMixin


class GridDesktop(GridMixin, MockGridDesktop):
    pass


class TestGridLayout:
    def test_layout_from_cell_size(self):
        d = GridDesktop()
        cols, rows, w, h = d._grid_layout(cell_size=200)
        assert cols == 10  # 1920 / 200 = 9.6 -> ceil = 10
        assert rows == 6   # 1080 / 200 = 5.4 -> ceil = 6
        assert w == 1920
        assert h == 1080

    def test_layout_from_saved_state(self):
        d = GridDesktop()
        d._grid_cols = 5
        d._grid_rows = 3
        cols, rows, w, h = d._grid_layout(cell_size=200)
        assert cols == 5
        assert rows == 3

    def test_layout_from_grid_param(self):
        d = GridDesktop()
        d._grid_cols = 5  # should be overridden by grid param
        d._grid_rows = 3
        cols, rows, w, h = d._grid_layout(cell_size=200, grid="8x4")
        assert cols == 8
        assert rows == 4

    def test_layout_large_cell_size(self):
        d = GridDesktop()
        cols, rows, _, _ = d._grid_layout(cell_size=960)
        assert cols == 2
        assert rows == 2


class TestCellCoords:
    def test_cell_1_center(self):
        d = GridDesktop()
        d._grid_cols = 10
        d._grid_rows = 6
        coords = d._cell_coords(1, "center", 200)
        # Cell 1 is top-left, center should be at (96, 90)
        assert coords["cell"] == 1
        assert coords["position"] == "center"
        assert 80 <= coords["x"] <= 110
        assert 75 <= coords["y"] <= 100

    def test_cell_last(self):
        d = GridDesktop()
        d._grid_cols = 10
        d._grid_rows = 6
        coords = d._cell_coords(60, "center", 200)  # last cell (10*6)
        assert coords["cell"] == 60
        # Should be in bottom-right area
        assert coords["x"] > 1700
        assert coords["y"] > 900

    def test_cell_positions(self):
        d = GridDesktop()
        d._grid_cols = 4
        d._grid_rows = 4
        center = d._cell_coords(1, "center", 200)
        tl = d._cell_coords(1, "top-left", 200)
        br = d._cell_coords(1, "bottom-right", 200)
        assert tl["x"] < center["x"] < br["x"]
        assert tl["y"] < center["y"] < br["y"]

    def test_cell_bounds(self):
        d = GridDesktop()
        d._grid_cols = 4
        d._grid_rows = 4
        coords = d._cell_coords(1, "center", 200)
        bounds = coords["cell_bounds"]
        assert bounds["x1"] == 0
        assert bounds["y1"] == 0
        assert bounds["x2"] == 480  # 1920/4
        assert bounds["y2"] == 270  # 1080/4

    def test_cell_with_grid_override(self):
        d = GridDesktop()
        coords = d._cell_coords(1, "center", 200, grid="2x2")
        # Cell 1 in 2x2 grid: center of top-left quadrant
        assert 400 <= coords["x"] <= 560
        assert 220 <= coords["y"] <= 320


class TestSetGrid:
    def test_set_grid_updates_state(self):
        d = GridDesktop()
        d.set_grid(8, 5)
        assert d._grid_cols == 8
        assert d._grid_rows == 5
        assert d.manager._grid_state["test-1"] == (8, 5)


class TestClickCell:
    def test_click_cell_returns_result(self):
        d = GridDesktop()
        d._grid_cols = 10
        d._grid_rows = 6
        result = d.click_cell(5, "center", 200)
        assert result["clicked"] is True
        assert result["cell"] == 5

    def test_move_to_cell(self):
        d = GridDesktop()
        d._grid_cols = 10
        d._grid_rows = 6
        result = d.move_to_cell(3, "center", 200)
        assert result["moved"] is True
        assert result["cell"] == 3

    def test_look_at_cell(self):
        d = GridDesktop()
        d._grid_cols = 10
        d._grid_rows = 6
        result = d.look_at_cell(7, "center", 200)
        assert result["looked"] is True
        assert result["cell"] == 7


class TestScreenshotGrid:
    def test_screenshot_grid_returns_image(self):
        d = GridDesktop()
        d.set_grid(10, 6)
        img_bytes = d.screenshot_grid(cell_size=200, size=1000)
        assert len(img_bytes) > 100
        # Should be valid JPEG
        assert img_bytes[:2] == b'\xff\xd8'

    def test_screenshot_grid_saves_state(self):
        d = GridDesktop()
        d.screenshot_grid(cell_size=200, size=500)
        assert d._grid_cols is not None
        assert d._grid_rows is not None
