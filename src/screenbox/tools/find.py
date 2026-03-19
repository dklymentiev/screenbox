"""desktop_debug dispatcher tool (debug/advanced element finding)."""
import json
import time
from typing import Optional


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    def _find_on_screen(desktop_id: str, query: str, click: bool = False) -> str:
        """Find UI element by text query using cascade: AT-SPI -> OCR -> Gemini Vision.

        Cascade strategy (stops at first match):
        1. AT-SPI accessibility tree -- instant, free, exact coords for buttons/menus/inputs
        2. OCR text match -- free, finds any visible text on screen
        3. Gemini Flash vision -- optional (requires OPENROUTER_API_KEY), finds visual elements

        Use click=True to automatically click the found element.

        Args:
            desktop_id: Desktop to search
            query: What to find (e.g. "File", "OK", "minimize", "search input")
            click: If True, click the element after finding it

        Returns:
            JSON with: found, x, y, method (atspi/ocr/vision), confidence, match
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        query_lower = query.lower().strip()

        # --- Strategy 1: AT-SPI ---
        try:
            atspi_result = d.accessibility_find(name=query)
            elements = atspi_result.get("elements", [])
            if elements:
                el = elements[0]
                x, y = el.get("x", 0), el.get("y", 0)
                w, h = el.get("width", 0), el.get("height", 0)
                cx, cy = x + w // 2, y + h // 2
                if cx > 0 and cy > 0:
                    result = {
                        "found": True, "x": cx, "y": cy,
                        "method": "atspi", "confidence": 100,
                        "match": el.get("name", query),
                        "role": el.get("role", ""),
                        "elapsed_ms": int((time.time() - t0) * 1000),
                    }
                    if click:
                        d.click(cx, cy)
                        result["clicked"] = True
                    log_action(desktop_id, "desktop_find_on_screen",
                               {"query": query, "click": click}, result, t0)
                    return json.dumps(result, indent=2)
        except Exception:
            pass

        # --- Strategy 2: OCR (tesseract) ---
        try:
            ocr_result = d.find_on_screen(query)
            if ocr_result.get("found") and ocr_result.get("matches"):
                m = ocr_result["matches"][0]
                cx = m["x"] + m["width"] // 2
                cy = m["y"] + m["height"] // 2
                if cx > 0 and cy > 0:
                    result = {
                        "found": True, "x": cx, "y": cy,
                        "method": "ocr", "confidence": int(m.get("confidence", 80)),
                        "match": m.get("text", query),
                        "elapsed_ms": int((time.time() - t0) * 1000),
                    }
                    if click:
                        d.click(cx, cy)
                        result["clicked"] = True
                    log_action(desktop_id, "desktop_find_on_screen",
                               {"query": query, "click": click}, result, t0)
                    return json.dumps(result, indent=2)
        except Exception:
            pass

        # --- Strategy 3: Gemini Vision (optional, requires API key) ---
        from ..vision import find_element, OPENROUTER_KEY
        if OPENROUTER_KEY:
            try:
                img_bytes = d.screenshot(quality=85)
                if img_bytes:
                    vision_result = find_element(img_bytes, query)
                    if vision_result.get("found"):
                        x, y = vision_result["x"], vision_result["y"]
                        result = {
                            "found": True, "x": x, "y": y,
                            "method": "vision", "confidence": 60,
                            "match": query,
                            "model": vision_result.get("model", "flash"),
                            "elapsed_ms": int((time.time() - t0) * 1000),
                        }
                        if click:
                            d.click(x, y)
                            result["clicked"] = True
                        log_action(desktop_id, "desktop_find_on_screen",
                                   {"query": query, "click": click}, result, t0)
                        return json.dumps(result, indent=2)
            except Exception:
                pass

        # --- Not found ---
        result = {
            "found": False, "query": query,
            "methods_tried": ["atspi", "ocr"] + (["vision"] if OPENROUTER_KEY else []),
            "elapsed_ms": int((time.time() - t0) * 1000),
        }
        log_action(desktop_id, "desktop_find_on_screen",
                   {"query": query, "click": click}, result, t0)
        return json.dumps(result, indent=2)

    def _find_element(desktop_id: str, element: str,
                      region: Optional[str] = None,
                      include_windows: bool = True,
                      model: str = "flash") -> str:
        """Find UI element using Gemini Flash vision (requires OPENROUTER_API_KEY).

        For most cases, use desktop_debug(action=on_screen) instead (free AT-SPI/OCR cascade).
        This tool is for visual elements that AT-SPI and OCR cannot find.

        Args:
            desktop_id: Desktop to search
            element: What to find (e.g. "Chrome logo", "red icon")
            region: Optional crop region as "x1,y1,x2,y2". Smaller = more accurate.
            include_windows: Include window positions as JSON context (default True)
            model: Vision model: "flash" (default, fastest), "sonnet", "haiku"

        Returns:
            JSON with: found, x, y (screen coordinates), model, elapsed_ms
        """
        from ..vision import find_element

        t0 = time.time()
        d = get_desktop(desktop_id)
        img_bytes = d.screenshot(quality=85)
        if not img_bytes:
            return json.dumps({"error": "Screenshot failed"})

        rgn = None
        if region:
            coords = [int(c) for c in region.split(",")]
            if len(coords) == 4:
                rgn = tuple(coords)

        context = None
        if include_windows:
            try:
                windows = d.list_windows()
                if windows:
                    context = json.dumps(windows, indent=2)
            except Exception:
                pass

        result = find_element(img_bytes, element, region=rgn,
                              context_json=context, model=model)

        log_action(desktop_id, "desktop_find_element",
                   {"element": element, "region": region, "model": model},
                   result, t0)
        return json.dumps(result, indent=2)

    def _hover(desktop_id: str, x: int, y: int, duration_ms: int = 0,
               observe: bool = True, radius: int = 150, zoom: int = 5,
               windows: bool = True, win_radius: int = 500) -> str:
        """Move mouse to coordinates and OCR the surrounding area.

        WARNING: This MOVES the cursor. In menus/submenus, moving cursor away from
        a parent item CLOSES its submenu. For menu navigation:
        1. click(menu_button) -- open menu
        2. hover(submenu_item, observe=false, duration_ms=500) -- expand submenu
        3. click(target) -- select item. Do NOT hover again -- it will close the submenu.

        Returns what's around the cursor:
        - elements: OCR text with absolute screen coordinates (cx, cy = center)
        - windows: all windows overlapping the cursor area, with geometry and z-index

        Use this to orient: move mouse, see text AND window layout around cursor.
        Use window geometry to calculate button positions mathematically.

        Args:
            desktop_id: Desktop
            x: X coordinate
            y: Y coordinate
            duration_ms: How long to hover in ms (default 0)
            observe: Run OCR around cursor (default True). Set False for plain move.
            radius: OCR area radius around cursor (default 150px)
            zoom: OCR zoom factor 1-5 (default 5, use 4-5 for tiny elements)
            windows: Return nearby windows with geometry and z-index (default True)
            win_radius: Window search radius around cursor (default 500px)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        d.hover(x, y, duration_ms)
        result = {"hovered": True, "x": x, "y": y}

        if observe:
            ocr = d.inspect_area(x, y, radius=radius, zoom=zoom, conf_threshold=25)
            result["elements"] = ocr.get("elements", [])
            result["count"] = ocr.get("count", 0)
            result["viewport"] = ocr.get("viewport")
            result["magnifier"] = ocr.get("magnifier")

        if windows:
            try:
                nearby = d.get_windows_near(x, y, radius=win_radius)
                result["windows"] = nearby
                result["windows_count"] = len(nearby)
            except Exception as e:
                result["windows"] = []
                result["windows_count"] = 0
                result["windows_error"] = str(e)

        parts = []
        if observe:
            parts.append(f"{result.get('count', 0)} elements")
        if windows:
            parts.append(f"{result.get('windows_count', 0)} windows")
        log_action(desktop_id, "desktop_hover", {"x": x, "y": y, "observe": observe, "windows": windows},
                   ", ".join(parts) if parts else "moved", t0)
        return json.dumps(result, indent=2)

    @mcp.tool()
    def desktop_debug(desktop_id: str, action: str,
                     query: str = "", text: str = "",
                     click: bool = False, index: int = 0,
                     element: str = "", region: str = "",
                     model: str = "flash",
                     app_name: str = "", role: str = "", name: str = "",
                     element_path: str = "",
                     cell: str = "", cols: int = 3, rows: int = 3,
                     path: str = "", start_x: int = 0, start_y: int = 0,
                     timeout_ms: int = 10000, max_depth: int = 3,
                     x: int = 0, y: int = 0,
                     radius: int = 150, observe: bool = True,
                     windows: bool = True,
                     intent: str = "",
                     step: str = "") -> str:
        """Debug/advanced element finding. NOT for normal agent workflow.

        Normal agents should use: screenshot -> look -> click.
        This tool is for debugging, accessibility inspection, and advanced automation.

        Actions:
          on_screen(query, click?): AT-SPI -> OCR -> Vision cascade
          text(text): Find text via OCR
          click_text(text, index?): Find text via OCR and click it
          wait_text(text, timeout_ms?): Wait for text to appear
          element(element, region?, model?): Find via AI vision
          a11y_apps: List accessible applications
          a11y_tree(app_name?, max_depth?): Get accessibility tree
          a11y_find(role?, name?, app_name?): Find accessible elements
          a11y_activate(app_name, element_path): Click accessible element
          a11y_set_text(app_name, element_path, text): Set text on element
          inspect_cell(cell, cols?, rows?): OCR grid cell with coordinates
          menu_click(path, start_x?, start_y?): Navigate menu "File > Save"
          hover(x, y, observe?, radius?, windows?): Move cursor + OCR area

        Args:
            desktop_id: Desktop to search
            action: Action name
            query: For on_screen
            text: For text/click_text/wait_text/a11y_set_text
            click: Auto-click found element (on_screen)
            index: Match index for click_text (0=first)
            element: Description for AI vision find
            region: Crop region "x1,y1,x2,y2" for element
            model: Vision model ("flash", "sonnet", "haiku")
            app_name: App name for a11y actions
            role: Element role for a11y_find
            name: Element name for a11y_find
            element_path: Path for a11y_activate/a11y_set_text
            cell: Grid cell for inspect_cell ("3" or "3,2")
            cols: Grid columns for inspect_cell (default 3)
            rows: Grid rows for inspect_cell (default 3)
            path: Menu path for menu_click ("File > Save")
            start_x: X hint for menu_click
            start_y: Y hint for menu_click
            timeout_ms: Timeout for wait_text (default 10000)
            max_depth: Max depth for a11y_tree (default 3)
            x: X for hover
            y: Y for hover
            radius: OCR radius for hover (default 150)
            observe: Do OCR around hover (default True)
            windows: Include window info in hover (default True)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)

        if action == "on_screen":
            return _find_on_screen(desktop_id, query, click)
        elif action == "text":
            result = d.find_on_screen(text)
        elif action == "click_text":
            result = d.click_on_text(text, index)
        elif action == "wait_text":
            result = d.wait_for_text(text, timeout_ms)
        elif action == "element":
            return _find_element(
                desktop_id, element, region or None, model=model)
        elif action == "a11y_apps":
            result = d.accessibility_apps()
        elif action == "a11y_tree":
            result = d.accessibility_tree(app_name or None, max_depth)
        elif action == "a11y_find":
            result = d.accessibility_find(
                role or None, name or None, app_name or None)
        elif action == "a11y_activate":
            result = d.accessibility_activate(app_name, element_path)
        elif action == "a11y_set_text":
            result = d.accessibility_set_text(app_name, element_path, text)
        elif action == "inspect_cell":
            result = d.inspect_cell(cell, cols=cols, rows=rows)
        elif action == "menu_click":
            result = d.menu_click(path, start_x, start_y)
        elif action == "hover":
            return _hover(
                desktop_id, x, y, observe=observe,
                radius=radius, windows=windows)
        else:
            return json.dumps({"error": f"Unknown find action: {action}",
                               "available": [
                                   "on_screen", "text", "click_text",
                                   "wait_text", "element", "a11y_apps",
                                   "a11y_tree", "a11y_find", "a11y_activate",
                                   "a11y_set_text", "inspect_cell",
                                   "menu_click", "hover"]})

        log_action(desktop_id, "desktop_debug", {"action": action}, result, t0, intent=intent, step=step)
        return json.dumps(result, indent=2)
