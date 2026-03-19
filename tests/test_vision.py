"""Unit tests for vision module (vision.py)."""

import io
import json
import pytest
from PIL import Image

from screenbox.vision import crop_screenshot, normalize_screenshot, grid_overlay


def _make_image(w=1920, h=1080, color=(50, 100, 150)):
    """Create a test JPEG image."""
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return buf.getvalue()


class TestCropScreenshot:
    def test_basic_crop(self):
        img_bytes = _make_image()
        cropped, meta = crop_screenshot(img_bytes, (100, 100, 500, 400))
        assert len(cropped) > 0
        assert meta["x1"] == 100
        assert meta["y1"] == 100
        assert meta["x2"] == 500
        assert meta["y2"] == 400
        assert meta["crop_w"] == 400
        assert meta["crop_h"] == 300
        assert meta["full_w"] == 1920
        assert meta["full_h"] == 1080

    def test_crop_clamped_to_bounds(self):
        img_bytes = _make_image(800, 600)
        cropped, meta = crop_screenshot(img_bytes, (-100, -50, 900, 700))
        assert meta["x1"] == 0
        assert meta["y1"] == 0
        assert meta["x2"] == 800
        assert meta["y2"] == 600

    def test_crop_returns_valid_jpeg(self):
        img_bytes = _make_image()
        cropped, _ = crop_screenshot(img_bytes, (0, 0, 100, 100))
        assert cropped[:2] == b'\xff\xd8'  # JPEG magic

    def test_crop_quality(self):
        img_bytes = _make_image()
        low_q, _ = crop_screenshot(img_bytes, (0, 0, 500, 500), quality=10)
        high_q, _ = crop_screenshot(img_bytes, (0, 0, 500, 500), quality=95)
        assert len(low_q) < len(high_q)

    def test_crop_small_region(self):
        img_bytes = _make_image()
        cropped, meta = crop_screenshot(img_bytes, (960, 540, 961, 541))
        assert meta["crop_w"] == 1
        assert meta["crop_h"] == 1


class TestNormalizeScreenshot:
    def test_normalize_to_width(self):
        img_bytes = _make_image(1920, 1080)
        normalized, meta = normalize_screenshot(img_bytes, width=1000)
        img = Image.open(io.BytesIO(normalized))
        assert img.size[0] == 1000
        assert meta["original_w"] == 1920
        assert meta["original_h"] == 1080
        assert meta["scale"] == round(1000 / 1920, 4)

    def test_normalize_pad_to_square(self):
        img_bytes = _make_image(1920, 1080)
        normalized, meta = normalize_screenshot(img_bytes, width=1000, pad_to_square=True)
        img = Image.open(io.BytesIO(normalized))
        assert img.size == (1000, 1000)
        assert meta["padded"] is True

    def test_normalize_no_padding(self):
        img_bytes = _make_image(1920, 1080)
        normalized, meta = normalize_screenshot(img_bytes, width=1000, pad_to_square=False)
        img = Image.open(io.BytesIO(normalized))
        assert img.size[0] == 1000
        assert img.size[1] < 1000  # 1080 * (1000/1920) = 562
        assert meta["padded"] is False

    def test_normalize_already_square(self):
        img_bytes = _make_image(1000, 1000)
        normalized, meta = normalize_screenshot(img_bytes, width=1000)
        img = Image.open(io.BytesIO(normalized))
        assert img.size == (1000, 1000)

    def test_normalize_upscale(self):
        img_bytes = _make_image(640, 480)
        normalized, meta = normalize_screenshot(img_bytes, width=1000)
        img = Image.open(io.BytesIO(normalized))
        assert img.size[0] == 1000
        assert meta["scale"] > 1.0


class TestGridOverlay:
    def test_grid_basic(self):
        img_bytes = _make_image()
        result, cells = grid_overlay(img_bytes, cols=3, rows=3)
        assert len(result) > 0
        assert result[:2] == b'\xff\xd8'
        assert len(cells) == 9  # 3x3

    def test_grid_cell_coordinates(self):
        img_bytes = _make_image(1200, 900)
        _, cells = grid_overlay(img_bytes, cols=3, rows=3)
        cell1 = cells["1"]
        assert cell1["x1"] == 0
        assert cell1["y1"] == 0
        assert cell1["x2"] == 400  # 1200/3
        assert cell1["y2"] == 300  # 900/3
        assert cell1["cx"] == 200  # center x
        assert cell1["cy"] == 150  # center y

    def test_grid_cell_9(self):
        img_bytes = _make_image(1200, 900)
        _, cells = grid_overlay(img_bytes, cols=3, rows=3)
        cell9 = cells["9"]
        assert cell9["x1"] == 800
        assert cell9["y1"] == 600
        assert cell9["x2"] == 1200
        assert cell9["y2"] == 900

    def test_grid_custom_size(self):
        img_bytes = _make_image()
        _, cells = grid_overlay(img_bytes, cols=4, rows=2)
        assert len(cells) == 8

    def test_grid_single_cell(self):
        img_bytes = _make_image(800, 600)
        _, cells = grid_overlay(img_bytes, cols=1, rows=1)
        assert len(cells) == 1
        assert cells["1"]["x1"] == 0
        assert cells["1"]["y1"] == 0

    def test_grid_preserves_image_size(self):
        img_bytes = _make_image(1920, 1080)
        result, _ = grid_overlay(img_bytes, cols=3, rows=3)
        img = Image.open(io.BytesIO(result))
        assert img.size == (1920, 1080)
