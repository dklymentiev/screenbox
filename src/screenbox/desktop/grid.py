"""Grid overlay and cell-based interaction mixin for Desktop.

Provides numbered grid overlay on screenshots, cell coordinate calculation,
and cell-based click/move/look operations.
"""

import base64 as _b64
import io as _io
import math
import logging

from PIL import Image, ImageDraw

log = logging.getLogger("screenbox.desktop")

DISPLAY = ":99"


class GridMixin:
    """Grid methods extracted from Desktop class.

    Expects the host class to provide:
        _exec(), _xdotool(), screenshot(), click(),
        get_cursor_position(), _adaptive_radius(), _cursor_ocr()
        _grid_cols, _grid_rows (instance vars on Desktop)
    """

    def _get_screen_dims(self) -> tuple[int, int]:
        """Get screen dimensions via xdpyinfo."""
        raw = self._exec(["env", f"DISPLAY={DISPLAY}", "xdpyinfo"], timeout=3)
        for line in raw.split("\n"):
            if "dimensions:" in line:
                parts = line.split("dimensions:")[1].strip().split()[0]
                return tuple(map(int, parts.split("x")))
        return 1920, 1080

    def set_grid(self, cols: int, rows: int):
        """Set the unified grid dimensions. Persisted in manager across Desktop instances."""
        self._grid_cols = cols
        self._grid_rows = rows
        self.manager._grid_state[self.desktop_id] = (cols, rows)
        log.info("Grid set to %dx%d (%d cells) for %s", cols, rows, cols * rows, self.desktop_id)

    def _grid_layout(self, cell_size: int = 0, grid: str = None,
                      target_cells: int = 60):
        """Calculate grid layout. Uses saved grid state if available.

        Priority: grid param ("10x6" or "60") > saved state > target_cells fallback.
        Returns (cols, rows, screen_w, screen_h).
        """
        screen_w, screen_h = self._get_screen_dims()

        if grid:
            g = grid.strip().lower()
            if "x" in g:
                parts = g.split("x")
                cols = int(parts[0]) if len(parts) >= 1 else 3
                rows = int(parts[1]) if len(parts) >= 2 else 3
            else:
                # Pure number = target cell count
                try:
                    target_cells = int(g)
                except ValueError:
                    pass
                aspect = screen_w / max(screen_h, 1)
                rows = max(1, round(math.sqrt(target_cells / aspect)))
                cols = max(1, round(target_cells / rows))
        elif self._grid_cols and self._grid_rows:
            cols = self._grid_cols
            rows = self._grid_rows
        elif cell_size > 0:
            cols = math.ceil(screen_w / cell_size)
            rows = math.ceil(screen_h / cell_size)
        else:
            aspect = screen_w / max(screen_h, 1)
            rows = max(1, round(math.sqrt(target_cells / aspect)))
            cols = max(1, round(target_cells / rows))

        return cols, rows, screen_w, screen_h

    def screenshot_grid(self, cell_size: int = 200, size: int = 1000,
                         quality: int = 80, grid: str = None) -> bytes:
        """Capture screen as square JPEG with numbered grid overlay.

        Square cells of cell_size x cell_size real pixels (or grid="COLSxROWS").
        Each cell numbered 1..N (left-to-right, top-to-bottom).
        Red cursor crosshair shows current mouse position.
        Saves grid dimensions to Desktop instance for look/click reuse.
        """
        raw = self.screenshot(quality=95)
        if not raw:
            return b""

        img = Image.open(_io.BytesIO(raw))
        real_w, real_h = img.size

        cols, rows, _, _ = self._grid_layout(cell_size, grid=grid)
        # Save grid state so look/click use the same grid
        self.set_grid(cols, rows)

        cursor = self.get_cursor_position()
        cur_x = cursor.get("X", 0)
        cur_y = cursor.get("Y", 0)

        scale = size / real_w
        scaled_h = int(real_h * scale)
        img_scaled = img.resize((size, scaled_h), Image.LANCZOS)

        canvas = Image.new("RGB", (size, size), (10, 14, 20))
        canvas.paste(img_scaled, (0, 0))
        draw = ImageDraw.Draw(canvas)

        # Cell size in image pixels (screen divided evenly, matches overlay)
        cell_w_img = size / cols
        cell_h_img = scaled_h / rows

        # Grid lines
        grid_color = (255, 255, 0, 180)  # yellow
        for i in range(cols + 1):
            x = int(i * cell_w_img)
            draw.line([x, 0, x, scaled_h], fill=grid_color, width=1)
        for j in range(rows + 1):
            y = int(j * cell_h_img)
            draw.line([0, y, size, y], fill=grid_color, width=1)

        # Cell numbers
        cell_num = 1
        for j in range(rows):
            for i in range(cols):
                cx = int(i * cell_w_img + cell_w_img / 2 - 8)
                cy = int(j * cell_h_img + cell_h_img / 2 - 8)
                draw.rectangle([cx - 2, cy - 2, cx + 20, cy + 14],
                               fill=(0, 0, 0, 200))
                draw.text((cx, cy), str(cell_num), fill=(255, 255, 0))
                cell_num += 1

        # Cursor crosshair
        ccx = int(cur_x * scale)
        ccy = int(cur_y * scale)
        arm = 15
        cursor_color = (255, 50, 50)
        draw.line([ccx - arm, ccy, ccx + arm, ccy], fill=cursor_color, width=2)
        draw.line([ccx, ccy - arm, ccx, ccy + arm], fill=cursor_color, width=2)
        draw.ellipse([ccx - 8, ccy - 8, ccx + 8, ccy + 8],
                     outline=cursor_color, width=2)

        # Which cell is cursor in?
        real_cell_w = real_w / cols
        real_cell_h = real_h / rows
        cur_col = min(int(cur_x / real_cell_w), cols - 1)
        cur_row = min(int(cur_y / real_cell_h), rows - 1)
        cur_cell = cur_row * cols + cur_col + 1

        # Info panel
        panel_y = scaled_h + 10
        draw.text((10, panel_y),
                  f"Grid: {cols}x{rows} ({cols * rows} cells)   "
                  f"Cell: {cell_size}x{cell_size}px   "
                  f"Cursor: ({cur_x},{cur_y}) = cell {cur_cell}",
                  fill=(160, 170, 190))

        buf = _io.BytesIO()
        canvas.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def _cell_coords(self, cell: int, position: str = "center",
                      cell_size: int = 200, grid: str = None) -> dict:
        """Calculate real screen coordinates for a grid cell + position.

        Uses saved grid state if available (set by screenshot with grid=).
        Falls back to cell_size-based calculation.
        """
        cols, rows, screen_w, screen_h = self._grid_layout(cell_size, grid=grid)

        # Actual cell dimensions (screen divided evenly, same as grid overlay)
        cell_w = screen_w / cols
        cell_h = screen_h / rows

        cell_idx = cell - 1
        col = cell_idx % cols
        row = cell_idx // cols

        offsets = {
            "center": (0.5, 0.5),
            "top-left": (0.15, 0.15),
            "top-right": (0.85, 0.15),
            "bottom-left": (0.15, 0.85),
            "bottom-right": (0.85, 0.85),
            "top": (0.5, 0.15),
            "bottom": (0.5, 0.85),
            "left": (0.15, 0.5),
            "right": (0.85, 0.5),
        }
        ox, oy = offsets.get(position, (0.5, 0.5))

        x1 = col * cell_w
        y1 = row * cell_h
        x = int(x1 + ox * cell_w)
        y = int(y1 + oy * cell_h)

        return {
            "x": x,
            "y": y,
            "cell": cell,
            "position": position,
            "cell_bounds": {
                "x1": int(x1), "y1": int(y1),
                "x2": int(x1 + cell_w), "y2": int(y1 + cell_h),
            },
        }

    def click_cell(self, cell: int, position: str = "center",
                   cell_size: int = 200, button: int = 1,
                   ocr_radius: int = 0, grid: str = None) -> dict:
        """Click within a numbered grid cell. Returns OCR around cursor."""
        coords = self._cell_coords(cell, position, cell_size, grid=grid)
        self.click(coords["x"], coords["y"], button)
        result = {"clicked": True, **coords}
        radius = self._adaptive_radius(ocr_radius)
        return self._cursor_ocr(result, radius=radius)

    def move_to_cell(self, cell: int, position: str = "center",
                     cell_size: int = 200, ocr_radius: int = 0,
                     grid: str = None) -> dict:
        """Move cursor to a position within a numbered grid cell. Returns OCR around cursor."""
        coords = self._cell_coords(cell, position, cell_size, grid=grid)
        self._xdotool("mousemove", str(coords["x"]), str(coords["y"]))
        result = {"moved": True, **coords}
        radius = self._adaptive_radius(ocr_radius)
        return self._cursor_ocr(result, radius=radius)

    def look_at_cell(self, cell: int, position: str = "center",
                     cell_size: int = 200, ocr_radius: int = 0,
                     grid: str = None) -> dict:
        """OCR around cell position WITHOUT moving the mouse. Scout before clicking."""
        coords = self._cell_coords(cell, position, cell_size, grid=grid)
        result = {"looked": True, **coords}
        radius = self._adaptive_radius(ocr_radius)
        return self._cursor_ocr(result, radius=radius)

    def dashboard_grid(self, on: bool = True, cols: int = 3, rows: int = 3,
                       cell_size: int = 0) -> str:
        """Toggle numbered grid overlay on the live VNC desktop (visual debug layer).

        This only affects what the user sees in VNC/dashboard.
        Screenshots and OCR always work on the clean desktop layer.

        Args:
            on: True to show grid, False to hide
            cols, rows: Grid dimensions
            cell_size: If > 0, auto-compute cols/rows from screen size
        """
        if not on:
            self._exec(["rm", "-f", "/tmp/.grid-overlay-config"])
            return "Dashboard grid: off"
        if cell_size > 0:
            cols, rows, _, _ = self._grid_layout(cell_size)
        self.set_grid(cols, rows)
        self._exec(["sh", "-c", "printf '%s' \"$1\" > /tmp/.grid-overlay-config", "sh", f"{cols},{rows}"])
        return f"Dashboard grid: on ({cols}x{rows})"

    # Keep old names as aliases for compatibility
    def grid_on(self, cols: int = 3, rows: int = 3, cell_size: int = 0) -> str:
        return self.dashboard_grid(on=True, cols=cols, rows=rows, cell_size=cell_size)

    def grid_off(self) -> str:
        return self.dashboard_grid(on=False)

    def grid_screenshot(self, cols: int = 3, rows: int = 3, quality: int = 75) -> dict:
        """Take screenshot and divide into numbered grid cells.

        Returns base64 image with grid overlay and cell coordinates.
        """
        img_bytes = self.screenshot(quality=quality)
        if not img_bytes:
            return {"error": "Screenshot failed"}

        # Use ImageMagick inside container to add grid overlay
        # Calculate grid dimensions
        # Get actual screen size from the image
        script = (
            f"echo $(identify -format '%wx%h' /dev/stdin < /dev/null 2>/dev/null || echo '1280x720')"
        )
        size_out = self._exec(["bash", "-c",
                                "DISPLAY=:99 xdpyinfo 2>/dev/null | grep dimensions | awk '{print $2}'"],
                               timeout=5).strip()
        if "x" in size_out:
            w, h = map(int, size_out.split("x")[:2])
        else:
            w, h = 1280, 720

        cell_w = w // cols
        cell_h = h // rows

        cells = {}
        for r in range(rows):
            for c in range(cols):
                idx = r * cols + c + 1
                cells[str(idx)] = {
                    "x": c * cell_w,
                    "y": r * cell_h,
                    "width": cell_w,
                    "height": cell_h,
                    "center_x": c * cell_w + cell_w // 2,
                    "center_y": r * cell_h + cell_h // 2,
                }

        b64 = _b64.b64encode(img_bytes).decode("ascii")
        return {
            "format": "jpeg",
            "base64": b64,
            "size": len(img_bytes),
            "grid": {"cols": cols, "rows": rows, "cell_width": cell_w, "cell_height": cell_h},
            "cells": cells,
        }
