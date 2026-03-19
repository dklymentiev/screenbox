"""desktop_look tool."""
import json
import logging
import time
from mcp.server.fastmcp import Image


logger = logging.getLogger(__name__)


from . import get_window_title as _get_window_title
from . import inject_knowledge as _inject_knowledge


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_look(desktop_id: str, cell: int = 0,
                     position: str = "center",
                     cell_size: int = 200,
                     ocr_radius: int = 0,
                     source: str = "mouse",
                     grid: str = "",
                     intent: str = "",
                     step: str = "") -> list:
        """OCR around grid cell or cursor. Returns REAL screen coordinates for clicking.

        WORKFLOW: desktop_screenshot -> desktop_look(cell=N) -> desktop_click(x, y)

        This is the ONLY way to get click coordinates. Screenshots are normalized
        to 1000x1000 -- coordinates from screenshots will be WRONG.

        Two modes:
        - cell=N (1+): Look at grid cell N. Uses last screenshot's grid automatically.
        - cell=0 (default): Look around current cursor position.

        Returns cropped image + text elements with REAL screen coordinates (cx, cy).
        Use these cx/cy values directly in desktop_click().

        Args:
            desktop_id: Desktop
            cell: Cell number (1+ = grid cell, 0 = cursor position)
            position: Where in cell to focus: center, top-left, top-right, bottom-left, bottom-right
            cell_size: Grid cell size in pixels (default 200, ignored if grid is saved from screenshot)
            ocr_radius: Override radius in pixels (0 = auto from screen resolution)
            source: What to look around: "mouse" (default), "caret" (text cursor via AT-SPI)
            grid: Override grid as "COLSxROWS" (e.g. "10x6"). If empty, uses last screenshot's grid.
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        if cell > 0:
            result = d.look_at_cell(cell, position, cell_size, ocr_radius,
                                    grid=grid or None)
        elif source == "caret":
            result = d.look_at_caret(ocr_radius)
        else:
            result = d.look_at_cursor(ocr_radius)

        # Include active grid info in response
        if d._grid_cols and d._grid_rows:
            result["grid"] = f"{d._grid_cols}x{d._grid_rows}"
            result["grid_cells"] = d._grid_cols * d._grid_rows

        # Build click tips
        tips = []
        for el in sorted(result.get("elements", []),
                         key=lambda e: e.get("conf", 0), reverse=True):
            if el.get("conf", 0) >= 50 and el.get("text", "").strip():
                tips.append(
                    f'desktop_click("{desktop_id}", {el["cx"]}, {el["cy"]})  # {el["text"]}'
                )
        tip_lines = []
        if tips:
            tip_lines.append("Click targets (use these EXACT coordinates):")
            tip_lines.extend(tips[:8])
            tip_lines.append("")
            tip_lines.append("IMPORTANT: Use ONLY the cx/cy coordinates from elements above.")
        else:
            # No elements found -- suggest cell center as fallback
            cx, cy = result.get("x", 0), result.get("y", 0)
            tip_lines.append("No text elements found in this area.")
            tip_lines.append(f'Fallback -- click cell center: desktop_click("{desktop_id}", {cx}, {cy})')
            tip_lines.append("The observe after click may detect nearby elements.")

        # Neighbor cell hints: when elements are near edges, suggest adjacent cells
        if cell > 0 and d._grid_cols and d._grid_rows:
            cols, rows = d._grid_cols, d._grid_rows
            total_cells = cols * rows
            radius = result.get("radius", 200)
            center_x, center_y = result.get("x", 0), result.get("y", 0)
            bounds = result.get("cell_bounds", {})

            edge_hints = []
            for el in result.get("elements", []):
                ey = el.get("cy", 0)
                ex = el.get("cx", 0)
                text = el.get("text", "").strip()
                if not text or el.get("conf", 0) < 30:
                    continue
                # Check if element is near the edge of the OCR crop
                if ey < center_y - radius + 20 and cell - cols >= 1:
                    neighbor = cell - cols
                    if neighbor not in [h[0] for h in edge_hints]:
                        edge_hints.append((neighbor, "above"))
                if ey > center_y + radius - 20 and cell + cols <= total_cells:
                    neighbor = cell + cols
                    if neighbor not in [h[0] for h in edge_hints]:
                        edge_hints.append((neighbor, "below"))
                if ex < center_x - radius + 20 and (cell - 1) % cols != 0:
                    neighbor = cell - 1
                    if neighbor not in [h[0] for h in edge_hints]:
                        edge_hints.append((neighbor, "left"))
                if ex > center_x + radius - 20 and cell % cols != 0:
                    neighbor = cell + 1
                    if neighbor not in [h[0] for h in edge_hints]:
                        edge_hints.append((neighbor, "right"))

            if edge_hints:
                tip_lines.append("")
                tip_lines.append("Elements may continue beyond this area:")
                for ncell, direction in edge_hints[:4]:
                    tip_lines.append(
                        f'  desktop_look("{desktop_id}", cell={ncell})  # see {direction}')

        result["tip"] = "\n".join(tip_lines)

        # Auto-inject knowledge hints based on OCR context
        ocr_words = []
        for el in result.get("elements", []):
            text = el.get("text", "").strip()
            if text and el.get("conf", 0) >= 40:
                ocr_words.extend(text.lower().split())
        hints = _inject_knowledge(desktop_id, d, ocr_words)
        if hints:
            result["knowledge_hints"] = hints

        # Extract image for multimodal return
        image_b64 = result.pop("image_base64", None)
        image_size = result.pop("image_size", None)
        if image_size:
            result["image_crop"] = f"{image_size['w']}x{image_size['h']}px"

        # Return as [Image, JSON] for multimodal display
        response = []
        if image_b64:
            import base64
            response.append(Image(data=base64.b64decode(image_b64), format="jpeg"))
        response.append(json.dumps(result, indent=2))
        log_action(desktop_id, "desktop_look",
                   {"cell": cell, "position": position},
                   {"elements_count": len(result.get("elements", []))}, t0,
                   intent=intent, step=step)
        return response
