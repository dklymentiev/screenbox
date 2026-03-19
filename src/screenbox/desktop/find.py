"""Find/search methods: OCR text location, smart click, wait conditions, menu navigation."""

import time


class FindMixin:
    """Screen search, text location, and menu navigation via OCR.

    Relies on host class providing:
        self.screenshot(), self._exec(), self.click(),
        self.inspect_area(), self._ext_cmd()
    """

    # ------------------------------------------------------------------
    # OCR text search
    # ------------------------------------------------------------------

    def find_on_screen(self, text: str) -> dict:
        """Find text on screen via screenshot + OCR (requires tesseract)."""
        # Take screenshot, run tesseract
        screenshot_data = self.screenshot(quality=95)
        if not screenshot_data:
            return {"found": False, "error": "Screenshot failed"}

        # Write temp file, run tesseract
        result = self._exec(
            ["bash", "-c",
             "import -window root -quality 95 png:- | "
             "tesseract stdin stdout -l eng tsv 2>/dev/null"],
            timeout=15,
        )
        if not result.strip():
            return {"found": False, "error": "OCR returned no results (tesseract may not be installed)"}

        matches = []
        for line in result.strip().split("\n")[1:]:  # skip header
            parts = line.split("\t")
            if len(parts) >= 12 and text.lower() in parts[11].lower():
                try:
                    matches.append({
                        "text": parts[11],
                        "x": int(parts[6]),
                        "y": int(parts[7]),
                        "width": int(parts[8]),
                        "height": int(parts[9]),
                        "confidence": float(parts[10]),
                    })
                except (ValueError, IndexError):
                    continue

        return {"found": len(matches) > 0, "matches": matches}

    def click_on_text(self, text: str, index: int = 0) -> dict:
        """Find text on screen via OCR and click its center."""
        result = self.find_on_screen(text)
        if not result["found"]:
            return {"clicked": False, "error": f"Text '{text}' not found on screen"}
        matches = result["matches"]
        if index >= len(matches):
            return {"clicked": False, "error": f"Index {index} out of range ({len(matches)} matches)"}
        m = matches[index]
        cx = m["x"] + m["width"] // 2
        cy = m["y"] + m["height"] // 2
        self.click(cx, cy)
        return {"clicked": True, "x": cx, "y": cy, "text": m["text"], "confidence": m["confidence"]}

    def wait_for_text(self, text: str, timeout_ms: int = 10000) -> dict:
        """Poll OCR until text appears on screen."""
        timeout_s = timeout_ms / 1000.0
        start = time.time()
        interval = 1.0  # OCR is expensive, don't poll too fast

        while time.time() - start < timeout_s:
            result = self.find_on_screen(text)
            if result["found"]:
                return {
                    "found": True,
                    "elapsed_ms": int((time.time() - start) * 1000),
                    "matches": result["matches"],
                }
            time.sleep(interval)

        return {"found": False, "elapsed_ms": int((time.time() - start) * 1000)}

    # ------------------------------------------------------------------
    # Smart click (approximate coords + OCR refinement)
    # ------------------------------------------------------------------

    def smart_click(self, x: int, y: int, target: str,
                    radius: int = 150, zoom: int = 4,
                    conf_threshold: int = 25, button: int = 1) -> dict:
        """Approximate click + OCR zoom = precise click.

        Point approximately where you want to click, name what you're looking for.
        Method crops the area, zooms, runs OCR, finds best match, clicks it.

        Args:
            x, y: Approximate screen coordinates (doesn't need to be exact).
            target: Text to find (e.g. "—" for minimize, "x" for close, "File", "Sign in").
            radius: Search radius in pixels around (x,y).
            zoom: OCR zoom factor (3-5 for small UI elements like title bar buttons).
            conf_threshold: Min OCR confidence.
            button: Mouse button (1=left).

        Returns:
            {clicked: bool, target, match: {text, cx, cy, conf}, elements: [...]}
        """
        result = self.inspect_area(x, y, radius=radius, zoom=zoom,
                                   conf_threshold=conf_threshold)
        if "error" in result:
            return result

        elements = result.get("elements", [])
        target_lower = target.lower()

        # Find best match: exact match first, then substring, then closest
        exact = [e for e in elements if e["text"].lower() == target_lower]
        partial = [e for e in elements if target_lower in e["text"].lower()
                   or e["text"].lower() in target_lower]

        match = None
        if exact:
            match = max(exact, key=lambda e: e["conf"])
        elif partial:
            match = max(partial, key=lambda e: e["conf"])

        if not match:
            return {
                "clicked": False,
                "target": target,
                "reason": "Target not found in OCR results",
                "elements": elements,
                "viewport": result.get("viewport"),
            }

        self.click(match["cx"], match["cy"], button)
        return {
            "clicked": True,
            "target": target,
            "match": match,
            "elements": elements,
            "viewport": result.get("viewport"),
        }

    # ------------------------------------------------------------------
    # Wait for conditions
    # ------------------------------------------------------------------

    def wait_for(self, condition: str, timeout_ms: int = 10000) -> dict:
        """Wait for a condition on the desktop.

        condition format: "url:example.com" or "title:Dashboard" or "selector:.my-element"
        Returns: {found: bool, elapsed_ms: int}
        """
        timeout_s = timeout_ms / 1000.0
        start = time.time()
        interval = 0.5

        cond_type, _, cond_value = condition.partition(":")
        cond_type = cond_type.strip().lower()
        cond_value = cond_value.strip()

        if cond_type == "selector":
            # Use extension waitFor
            try:
                result = self._ext_cmd("waitFor", {
                    "selector": cond_value, "timeout": timeout_ms
                })
                return {
                    "found": result.get("found", result.get("success", False)),
                    "elapsed_ms": int((time.time() - start) * 1000),
                }
            except Exception as e:
                return {"found": False, "error": str(e)}

        if cond_type not in ("url", "title"):
            return {"found": False, "error": f"Unknown condition: {cond_type}. Use url:, title:, or selector:"}

        while time.time() - start < timeout_s:
            try:
                info = self._ext_cmd("getPageInfo", timeout=3)
                value = info.get(cond_type, "")
                if cond_value.lower() in value.lower():
                    return {"found": True, "elapsed_ms": int((time.time() - start) * 1000),
                            "value": value}
            except Exception:
                pass
            time.sleep(interval)

        return {"found": False, "elapsed_ms": int((time.time() - start) * 1000)}

    # ------------------------------------------------------------------
    # Menu navigation
    # ------------------------------------------------------------------

    def menu_click(self, path: str, start_x: int = 0, start_y: int = 0) -> dict:
        """Navigate and click through a menu path like 'File > Save As'.

        Clicks each item in sequence with delays for submenu expansion.
        Uses OCR to find menu item text.

        Args:
            path: Menu path separated by ' > '
            start_x: X hint for first item (0 = auto-find)
            start_y: Y hint for first item (0 = auto-find)
        """
        items = [item.strip() for item in path.split(">")]
        if not items:
            return {"error": "Empty menu path"}

        def _sim(a: str, b: str) -> float:
            if not a or not b:
                return 0.0
            a, b = a.lower(), b.lower()
            if a == b:
                return 1.0
            if a in b or b in a:
                return 0.8
            shared = sum(1 for c in a if c in b)
            return shared / max(len(a), len(b))

        def _find_element(elements: list, text: str):
            tl = text.lower()
            for el in elements:
                if el.get("text", "").lower().strip() == tl:
                    return el
            candidates = []
            for el in elements:
                etxt = el.get("text", "").lower()
                if tl in etxt or etxt in tl:
                    candidates.append(el)
            if candidates:
                candidates.sort(key=lambda e: abs(len(e.get("text", "")) - len(text)))
                return candidates[0]
            best_score, best_el = 0.0, None
            for el in elements:
                score = _sim(tl, el.get("text", "").strip())
                if score > best_score:
                    best_score, best_el = score, el
            if best_score >= 0.6:
                return best_el
            return None

        clicked = []

        for i, item_text in enumerate(items):
            if i == 0 and start_x > 0 and start_y > 0:
                result = self.inspect_area(start_x, start_y, radius=200, zoom=3)
                elements = result.get("elements", [])
            elif clicked:
                prev = clicked[-1]
                scan_x = prev["x"]
                scan_y = prev["y"] + 120
                result = self.inspect_area(scan_x, scan_y, radius=250, zoom=3)
                all_elements = result.get("elements", [])
                min_y = prev["y"] + 10
                elements = [e for e in all_elements if e.get("cy", 0) > min_y]
                if not elements:
                    elements = all_elements
            else:
                elements = []

            match = _find_element(elements, item_text)

            if not match:
                return {
                    "error": f"Menu item '{item_text}' not found",
                    "clicked_so_far": clicked,
                    "elements_seen": [e.get("text") for e in elements[:10]],
                }

            mx, my = match["cx"], match["cy"]
            self.click(mx, my)
            clicked.append({"text": item_text, "x": mx, "y": my})

            if i < len(items) - 1:
                time.sleep(0.3)

        return {"clicked": True, "path": path, "items": clicked}
