"""desktop_screenshot tool."""
import json
import io as _io
import logging
import time
from typing import Optional

from mcp.server.fastmcp import Image
from PIL import Image as PILImage


logger = logging.getLogger(__name__)

DEFAULT_CELLS = 60


def _calc_grid(width: int, height: int, target_cells: int = DEFAULT_CELLS) -> tuple[int, int]:
    """Calculate grid cols x rows for a target number of cells, preserving aspect ratio."""
    import math
    aspect = width / max(height, 1)
    rows = max(1, round(math.sqrt(target_cells / aspect)))
    cols = max(1, round(target_cells / rows))
    return cols, rows


def _parse_grid(grid: str, width: int, height: int) -> tuple[int, int]:
    """Parse grid parameter: 'auto', '60' (target cells), '10x6' (explicit)."""
    g = grid.strip().lower()
    if g == "auto":
        return _calc_grid(width, height)
    if "x" in g:
        parts = g.split("x")
        return int(parts[0]), int(parts[1]) if len(parts) >= 2 else 3
    # Pure number = target cell count
    try:
        return _calc_grid(width, height, int(g))
    except ValueError:
        return _calc_grid(width, height)


from . import get_window_title as _get_window_title
from . import inject_knowledge as _inject_knowledge


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    def _smart_screenshot(desktop_id: str,
                          cell: Optional[str] = None,
                          region: Optional[str] = None,
                          normalize_width: Optional[int] = None,
                          grid: Optional[str] = None,
                          enhance: Optional[bool] = None) -> Image:
        """Screenshot with cell zoom, grid overlay, normalization, and contrast enhancement.

        RECOMMENDED WORKFLOW (no coordinate math needed):
          1. Call with grid="3x3" to see numbered cells on full screen
          2. Call with cell="3" to zoom into cell 3 (auto-normalized to 1000px)
          3. Call with cell="3,2" for 2-level zoom (cell 3 then sub-cell 2)
          4. See the element clearly, then desktop_click or desktop_smart_click

        Cell numbering (3x3 grid):
          [1][2][3]
          [4][5][6]
          [7][8][9]

        Args:
            desktop_id: Desktop to screenshot
            cell: Zoom into cell by number. "3" = cell 3, "3,2" = cell 3 then sub-cell 2.
                  Grid is 3x3 by default (override with grid param). Auto-normalizes to 1000px.
            region: Crop region as "x1,y1,x2,y2". Use cell= instead when possible.
            normalize_width: Resize to this width (default: 1000 when cell is used).
            grid: Grid overlay as "COLSxROWS" (e.g. "3x3"). Shows numbered cells.
            enhance: Boost contrast for dark themes.
        """
        from ..vision import crop_screenshot, normalize_screenshot, grid_overlay, enhance_screenshot
        t0 = time.time()
        d = get_desktop(desktop_id)
        img_bytes = d.screenshot(quality=85, _log=False)
        if not img_bytes:
            logger.error("Screenshot empty for %s", desktop_id)
            return json.dumps({"error": "Screenshot failed"})

        meta = {}

        # Cell zoom: crop by cell number(s) from grid
        if cell:
            img = PILImage.open(_io.BytesIO(img_bytes))
            # Parse grid dimensions -- default ~60 cells adaptive to resolution
            w_full, h_full = img.size
            if grid:
                g_cols, g_rows = _parse_grid(grid, w_full, h_full)
            else:
                g_cols, g_rows = _calc_grid(w_full, h_full)

            # Save grid state so look/click use the same grid
            d.set_grid(g_cols, g_rows)

            cells_path = [int(c) for c in cell.split(",")][:3]
            abs_x, abs_y = 0, 0
            for cell_num in cells_path:
                w, h = img.size
                cell_h = h // g_rows
                c_cols = max(1, round(w / cell_h)) if w != h else g_cols
                cell_w = w // c_cols
                c_row = (cell_num - 1) // c_cols
                c_col = (cell_num - 1) % c_cols
                left = c_col * cell_w
                top = c_row * cell_h
                abs_x += left
                abs_y += top
                img = img.crop((left, top, left + cell_w, top + cell_h))

            meta["cell"] = cell
            meta["cell_origin"] = [abs_x, abs_y]
            meta["cell_size"] = [img.size[0], img.size[1]]

            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=90)
            img_bytes = buf.getvalue()

            # Auto-normalize cell to 1000px if not specified
            if not normalize_width:
                normalize_width = 1000

        # Crop if requested (region= is alternative to cell=)
        elif region:
            coords = [int(c) for c in region.split(",")]
            if len(coords) == 4:
                img_bytes, crop_meta = crop_screenshot(img_bytes, tuple(coords))
                meta.update(crop_meta)

        # Grid overlay (only when NOT zooming into a cell)
        # "auto" = use saved grid or calculate from screen resolution
        if grid and not cell:
            if grid.lower() == "auto" and d._grid_cols and d._grid_rows:
                cols, rows = d._grid_cols, d._grid_rows
            else:
                _tmp = PILImage.open(_io.BytesIO(img_bytes))
                cols, rows = _parse_grid(grid, _tmp.size[0], _tmp.size[1])
            img_bytes, cells = grid_overlay(img_bytes, cols, rows)
            meta["grid"] = f"{cols}x{rows}"
            meta["cells"] = cells
            # Save grid state on Desktop so look/click use the same grid
            d.set_grid(cols, rows)

        # Enhance if requested (auto-invert dark themes, boost contrast/edges)
        if enhance:
            img_bytes, enh_meta = enhance_screenshot(img_bytes)
            meta.update(enh_meta)

        # Normalize if requested
        if normalize_width:
            img_bytes, norm_meta = normalize_screenshot(img_bytes, normalize_width)
            meta.update(norm_meta)

        return Image(data=img_bytes, format="jpeg")

    @mcp.tool()
    def desktop_screenshot(desktop_id: str,
                           region: str = "",
                           cell: str = "",
                           grid: str = "",
                           enhance: bool = False,
                           normalize_width: int = 0,
                           intent: str = "",
                           step: str = ""):
        """Screenshot the desktop. Returns normalized 1000x1000 image with grid overlay.

        WORKFLOW:
          1. desktop_screenshot(grid="10x6") -- set grid ONCE, see numbered cells
          2. desktop_look(cell=N) -- get REAL click coordinates for a cell
          3. desktop_click(x, y) -- click using coordinates FROM desktop_look

        Grid is remembered. After step 1, just call desktop_screenshot() with no
        params to see current state -- the same grid will be reused automatically.
        Only pass grid= again if you want to CHANGE the grid dimensions.

        NEVER guess coordinates from this image -- it is resized to 1000x1000.

        Args:
            desktop_id: Desktop to screenshot
            region: Crop region "x1,y1,x2,y2"
            cell: Grid cell to zoom into ("3" or "3,2" for nested zoom)
            grid: Grid overlay "COLSxROWS" (e.g. "10x6"). Set once, remembered for future calls.
            enhance: Boost contrast for dark themes
            normalize_width: Resize width (default 1000, rarely needed)
        """
        t0 = time.time()
        # Auto-grid: if no params specified, use saved grid or calculate from resolution
        if not (cell or region or grid or enhance or normalize_width):
            d = get_desktop(desktop_id)
            if d._grid_cols and d._grid_rows:
                grid = f"{d._grid_cols}x{d._grid_rows}"
            else:
                grid = "auto"
        # Always normalize to 1000px square (pad with black) unless explicitly set
        if not normalize_width:
            normalize_width = 1000
        rv = _smart_screenshot(
            desktop_id,
            cell=cell or None,
            region=region or None,
            normalize_width=normalize_width,
            grid=grid or None,
            enhance=enhance or None,
        )
        log_action(desktop_id, "desktop_screenshot",
                   {"region": region, "cell": cell, "grid": grid, "enhance": enhance},
                   "[image]", t0, intent=intent, step=step)

        # Workflow hint: tell the agent what to do next
        d = get_desktop(desktop_id)
        hints_parts = []

        # Grid workflow hint
        if grid and not cell:
            g = grid if grid != "auto" else (f"{d._grid_cols}x{d._grid_rows}" if d._grid_cols else "auto")
            hints_parts.append(
                f"Grid: {g}. Cells are numbered on the image.\n"
                "NEXT STEP: Pick a cell number and call desktop_look(cell=N) to read text and get click coordinates.\n"
                "DO NOT guess coordinates from this image. The image is normalized -- coordinates will be wrong.\n"
                "ONLY use coordinates returned by desktop_look or desktop_click."
            )
        elif cell:
            hints_parts.append(
                "Cell zoom active. To click an element, call desktop_look(cell=N) first to get exact coordinates,\n"
                "then desktop_click(x, y) with coordinates from the look response."
            )

        # Auto-inject knowledge hints for active app
        knowledge = _inject_knowledge(desktop_id, d)
        if knowledge:
            hints_parts.append(knowledge)

        # Auto-inject recent actions context
        from ..globals import format_recent_actions
        recent = format_recent_actions(desktop_id, limit=8)
        if recent:
            hints_parts.append(recent)

        if hints_parts:
            return [rv, "\n\n".join(hints_parts)]
        return rv
