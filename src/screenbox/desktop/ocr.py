"""OCR methods extracted from Desktop: cursor OCR, caret detection, cell/area inspection."""

import base64
import io as _io
import json
import logging
import os as _os
import shlex as _shlex_mod

_shlex_quote = _shlex_mod.quote

import cv2
import numpy as np
import pytesseract
from PIL import Image
from pytesseract import Output

log = logging.getLogger("screenbox.desktop")

OCR_RADIUS = 200  # legacy default for non-cell methods


class OCRMixin:
    """OCR-related methods for Desktop.

    Expects the composing class to provide:
        self.screenshot(quality=...)
        self.get_cursor_position()
        self.get_mouse_pos()
        self._exec(cmd, timeout=...)
        self._atspi_exec(script, timeout=...)
        self._adaptive_radius(ocr_radius)
        self._cell_coords(cell, position, cell_size)
        self._xdotool(...)
        self.desktop_id
    """

    def _cursor_ocr(self, coords: dict, radius: int = OCR_RADIUS,
                    include_image: bool = True) -> dict:
        """Run OCR around position and attach elements + optional cropped image.

        Args:
            coords: dict with x, y keys (center point)
            radius: OCR capture radius in pixels
            include_image: if True, attach base64 JPEG of cropped area
        """
        cx, cy = coords["x"], coords["y"]
        # Single screenshot reused for both OCR and image crop
        img_bytes = self.screenshot(quality=85)
        # Clamp coordinates to screen bounds (prevents PIL crop crash)
        if img_bytes:
            _img_tmp = Image.open(_io.BytesIO(img_bytes))
            sw, sh = _img_tmp.size
            if cx < 0 or cy < 0 or cx >= sw or cy >= sh:
                coords["warning"] = (
                    f"Cell coordinates ({cx},{cy}) are outside screen bounds ({sw}x{sh}). "
                    "Grid mismatch -- call desktop_screenshot with grid= to reset."
                )
                cx = max(0, min(cx, sw - 1))
                cy = max(0, min(cy, sh - 1))
                coords["x"], coords["y"] = cx, cy
        # Auto-zoom: target ~1000px output for good OCR
        zoom = max(4, 1000 // (radius * 2))
        ocr = self.inspect_area(cx, cy, radius=radius, zoom=zoom,
                                conf_threshold=25, _img_bytes=img_bytes)
        coords["elements"] = ocr.get("elements", [])
        coords["count"] = ocr.get("count", 0)
        coords["radius"] = radius
        coords["overlapped_cells"] = ocr.get("overlapped_cells", [])

        # Attach cropped image for visual context (reuses same screenshot)
        if include_image and img_bytes:
            try:
                img = Image.open(_io.BytesIO(img_bytes)).convert("RGB")
                full_w, full_h = img.size
                x1 = max(0, cx - radius)
                y1 = max(0, cy - radius)
                x2 = min(full_w, cx + radius)
                y2 = min(full_h, cy + radius)
                crop = img.crop((x1, y1, x2, y2))
                buf = _io.BytesIO()
                crop.save(buf, format="JPEG", quality=80)
                coords["image_base64"] = base64.b64encode(buf.getvalue()).decode()
                coords["image_size"] = {"w": x2 - x1, "h": y2 - y1}
            except Exception:
                pass

        return coords

    def look_at_cursor(self, ocr_radius: int = 0) -> dict:
        """OCR around current cursor position WITHOUT moving it.

        Returns detected text elements + cropped image around cursor.
        Useful for checking results after a click or quick orientation.
        """
        pos = self.get_cursor_position()
        cx, cy = pos.get("X", 0), pos.get("Y", 0)
        result = {"looked": True, "x": cx, "y": cy, "mode": "cursor"}
        radius = self._adaptive_radius(ocr_radius)
        return self._cursor_ocr(result, radius=radius)

    def get_caret_position(self) -> dict:
        """Get text caret position via AT-SPI focused widget.

        Finds the currently focused widget with a Text interface,
        queries the caret offset and character extents to get screen coordinates.
        Returns dict with x, y (screen coords of caret) or empty dict if not found.
        """
        script = (
            "import gi, json\n"
            "gi.require_version('Atspi', '2.0')\n"
            "from gi.repository import Atspi\n"
            "def find_focused(obj, depth=0):\n"
            "    if depth > 15: return None\n"
            "    try:\n"
            "        ss = obj.get_state_set()\n"
            "        if ss.contains(Atspi.StateType.FOCUSED):\n"
            "            return obj\n"
            "    except: pass\n"
            "    try:\n"
            "        for i in range(obj.get_child_count()):\n"
            "            c = obj.get_child_at_index(i)\n"
            "            if c:\n"
            "                r = find_focused(c, depth+1)\n"
            "                if r: return r\n"
            "    except: pass\n"
            "    return None\n"
            "desktop = Atspi.get_desktop(0)\n"
            "result = {'found': False}\n"
            "for i in range(desktop.get_child_count()):\n"
            "    app = desktop.get_child_at_index(i)\n"
            "    if not app: continue\n"
            "    focused = find_focused(app)\n"
            "    if not focused: continue\n"
            "    try:\n"
            "        ti = focused.queryText()\n"
            "        offset = ti.caretOffset\n"
            "        rect = ti.getCharacterExtents(offset, Atspi.CoordType.SCREEN)\n"
            "        result = {'found': True, 'x': rect.x, 'y': rect.y,\n"
            "                  'w': rect.width, 'h': rect.height, 'offset': offset,\n"
            "                  'app': app.get_name() or '', 'widget': focused.get_name() or '',\n"
            "                  'role': focused.get_role_name() or ''}\n"
            "        break\n"
            "    except: continue\n"
            "print(json.dumps(result))\n"
        )
        try:
            raw = self._atspi_exec(script, timeout=10)
            data = json.loads(raw.strip().split("\n")[-1])
            return data
        except Exception:
            return {"found": False}

    def look_at_caret(self, ocr_radius: int = 0) -> dict:
        """OCR around text caret position (not mouse cursor).

        Uses AT-SPI to find the focused text widget's caret position,
        then runs OCR around that point. Falls back to mouse cursor if
        caret not found.
        """
        caret = self.get_caret_position()
        if caret.get("found") and caret.get("x", 0) > 0:
            cx, cy = caret["x"], caret["y"]
            result = {"looked": True, "x": cx, "y": cy, "mode": "caret",
                      "caret_offset": caret.get("offset", -1),
                      "widget": caret.get("widget", ""),
                      "role": caret.get("role", "")}
        else:
            # Fallback to mouse cursor
            pos = self.get_cursor_position()
            cx, cy = pos.get("X", 0), pos.get("Y", 0)
            result = {"looked": True, "x": cx, "y": cy, "mode": "cursor",
                      "caret_fallback": True}
        radius = self._adaptive_radius(ocr_radius)
        return self._cursor_ocr(result, radius=radius)

    def inspect_cell(self, cell: str, cols: int = 3, rows: int = 3,
                     conf_threshold: int = 30) -> dict:
        """Crop grid cell (1-2 levels), run OCR, return elements with absolute screen coords.

        Args:
            cell: Cell path, e.g. "2" or "2,5" (2-level zoom max).
            cols: Grid columns (default 3).
            rows: Grid rows (default 3).
            conf_threshold: Min OCR confidence (0-100).

        Returns:
            {elements: [{text, x, y, w, h, cx, cy, conf}], viewport, breadcrumbs, count}
            All coordinates are absolute screen pixels.
        """
        img_bytes = self.screenshot(quality=95)
        if not img_bytes:
            return {"error": "Screenshot failed"}

        img = Image.open(_io.BytesIO(img_bytes)).convert("RGB")
        abs_x, abs_y = 0, 0
        breadcrumbs = []

        # Zoom into cell(s) -- max 2 levels
        cells = [int(c) for c in cell.split(",")][:2]
        for cell_num in cells:
            w, h = img.size
            cell_h = h // rows
            cell_w = w // cols
            c_row = (cell_num - 1) // cols
            c_col = (cell_num - 1) % cols
            left = c_col * cell_w
            top = c_row * cell_h
            abs_x += left
            abs_y += top
            breadcrumbs.append({
                "cell": cell_num,
                "grid": f"{cols}x{rows}",
                "abs": [abs_x, abs_y, cell_w, cell_h],
            })
            img = img.crop((left, top, left + cell_w, top + cell_h))

        crop_w, crop_h = img.size

        # Prepare for OCR: grayscale, dark theme inversion, upscale
        gray = np.array(img.convert("L"))
        if np.median(gray) < 128:
            gray = 255 - gray

        proc = Image.fromarray(gray)
        scale = 1
        if proc.width < 800:
            scale = max(2, 1000 // proc.width)
            proc = proc.resize((proc.width * scale, proc.height * scale), Image.LANCZOS)

        # OCR on host via pytesseract
        data = pytesseract.image_to_data(proc, lang="eng", config="--psm 6",
                                         output_type=Output.DICT)

        elements = []
        for i in range(len(data["text"])):
            try:
                conf = int(data["conf"][i])
                text = data["text"][i].strip()
            except (ValueError, IndexError):
                continue
            if conf < conf_threshold or not text:
                continue

            # OCR coords are in upscaled image -- convert back
            ox = data["left"][i] // scale
            oy = data["top"][i] // scale
            ow = data["width"][i] // scale
            oh = data["height"][i] // scale

            elements.append({
                "text": text,
                "x": abs_x + ox,
                "y": abs_y + oy,
                "w": ow,
                "h": oh,
                "cx": abs_x + ox + ow // 2,
                "cy": abs_y + oy + oh // 2,
                "conf": conf,
            })

        return {
            "elements": elements,
            "count": len(elements),
            "viewport": [abs_x, abs_y, crop_w, crop_h],
            "breadcrumbs": breadcrumbs,
        }

    def observe(self, radius: int = 150, zoom: int = 4,
                conf_threshold: int = 25) -> dict:
        """OCR around current mouse position. Move mouse approximately where you need, then call this.

        Returns same format as inspect_area: {elements, count, viewport, center, zoom}
        """
        mx, my = self.get_mouse_pos()
        result = self.inspect_area(mx, my, radius=radius, zoom=zoom,
                                   conf_threshold=conf_threshold)
        result["mouse"] = [mx, my]
        return result

    def inspect_area(self, x: int, y: int, radius: int = OCR_RADIUS,
                     zoom: int = 3, conf_threshold: int = 30,
                     psm: int = 6, _img_bytes: bytes | None = None) -> dict:
        """Crop area around (x,y), zoom it, run OCR, return elements with absolute screen coords.

        Args:
            x, y: Center point (screen pixels).
            radius: Crop radius in pixels (captures 2*radius square).
            zoom: Upscale factor (2-5x). Higher = better OCR on small elements.
            conf_threshold: Min OCR confidence (0-100).

        Returns:
            {elements: [{text, x, y, w, h, cx, cy, conf}], viewport, zoom, count}
            All coordinates are absolute screen pixels.
        """
        if _img_bytes is None:
            _img_bytes = self.screenshot(quality=95)

        if not _img_bytes:
            return {"error": "Screenshot failed"}

        img = Image.open(_io.BytesIO(_img_bytes)).convert("RGB")
        full_w, full_h = img.size

        # Fixed-size crop with black padding at screen edges.
        # Mouse is always at center of crop. Off-screen areas are black.
        crop_size = radius * 2
        canvas = Image.new("RGB", (crop_size, crop_size), (0, 0, 0))

        # Source region (may extend beyond screen)
        src_x1 = max(0, x - radius)
        src_y1 = max(0, y - radius)
        src_x2 = min(full_w, x + radius)
        src_y2 = min(full_h, y + radius)

        # Paste position on canvas (offset when near edge)
        paste_x = src_x1 - (x - radius)
        paste_y = src_y1 - (y - radius)

        canvas.paste(img.crop((src_x1, src_y1, src_x2, src_y2)), (paste_x, paste_y))

        crop = canvas

        # Absolute origin for coordinate conversion
        x1 = x - radius
        y1 = y - radius
        crop_w, crop_h = crop.size

        # Grayscale + dark theme inversion
        gray = np.array(crop.convert("L"))
        if np.median(gray) < 128:
            gray = 255 - gray

        # Debug: save intermediate images
        debug_dir = "/tmp/ocr-debug"
        _os.makedirs(debug_dir, exist_ok=True)
        crop.save(f"{debug_dir}/1-crop-rgb.jpg", quality=95)
        Image.fromarray(gray).save(f"{debug_dir}/2-gray-inverted.jpg", quality=95)

        # Zoom
        proc = Image.fromarray(gray)
        zoom = max(1, min(zoom, 5))
        if zoom > 1:
            proc = proc.resize((proc.width * zoom, proc.height * zoom), Image.LANCZOS)

        proc.save(f"{debug_dir}/3-zoomed-final.jpg", quality=95)

        # OCR with configurable PSM (default 6 = single block)
        def _run_ocr(image, psm_mode):
            data = pytesseract.image_to_data(image, lang="eng", config=f"--psm {psm_mode}",
                                             output_type=Output.DICT)
            results = []
            for i in range(len(data["text"])):
                try:
                    conf = int(data["conf"][i])
                    text_val = data["text"][i].strip()
                except (ValueError, IndexError):
                    continue
                if conf < conf_threshold or not text_val:
                    continue
                ox = data["left"][i] // zoom
                oy = data["top"][i] // zoom
                ow = data["width"][i] // zoom
                oh = data["height"][i] // zoom
                if ox < 0 or oy < 0 or ox + ow > crop_w or oy + oh > crop_h:
                    continue
                results.append({
                    "text": text_val,
                    "x": x1 + ox, "y": y1 + oy,
                    "w": ow, "h": oh,
                    "cx": x1 + ox + ow // 2, "cy": y1 + oy + oh // 2,
                    "conf": conf,
                })
            return results

        # Pass 1: Full-image OCR (good for text)
        elements = _run_ocr(proc, psm)
        max_area = crop_w * crop_h * 0.5
        elements = [e for e in elements if e["w"] * e["h"] < max_area]

        # Pass 2: Sobel + contours + PSM 10 (good for icons/buttons)
        # Run on zoomed image for better edge detection
        zoomed_arr = np.array(proc)  # proc = zoomed grayscale (already inverted)
        sobelx = cv2.Sobel(zoomed_arr, cv2.CV_64F, 1, 0, ksize=3)
        sobely = cv2.Sobel(zoomed_arr, cv2.CV_64F, 0, 1, ksize=3)
        sobel = np.sqrt(sobelx**2 + sobely**2)
        sobel = np.clip(sobel, 0, 255).astype(np.uint8)
        _, s_thresh = cv2.threshold(sobel, 30, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(s_thresh, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)

        # Debug: save Sobel pipeline stages
        Image.fromarray(sobel).save(f"{debug_dir}/4-sobel.jpg", quality=95)
        Image.fromarray(s_thresh).save(f"{debug_dir}/5-sobel-thresh.jpg", quality=95)
        contour_vis = cv2.cvtColor(zoomed_arr, cv2.COLOR_GRAY2BGR)
        cv2.drawContours(contour_vis, contours, -1, (0, 255, 0), 1)
        Image.fromarray(contour_vis).save(f"{debug_dir}/6-contours.jpg", quality=95)

        # Collect existing element centers for dedup
        existing = {(e["cx"], e["cy"]) for e in elements}
        pad = 4 * zoom
        for c in contours:
            bx, by, bw, bh = cv2.boundingRect(c)
            # Size filter in zoomed pixels (original thresholds * zoom)
            min_sz, max_sz = 8 * zoom, 80 * zoom
            min_area = 50 * zoom * zoom
            if not (min_sz < bw < max_sz and min_sz < bh < max_sz and cv2.contourArea(c) > min_area):
                continue
            # Convert back to screen coordinates
            scr_bx = bx // zoom
            scr_by = by // zoom
            scr_bw = bw // zoom
            scr_bh = bh // zoom
            bcx, bcy = x1 + scr_bx + scr_bw // 2, y1 + scr_by + scr_bh // 2
            # Skip if too close to an existing element (within 15px)
            if any(abs(bcx - ex) < 15 and abs(bcy - ey) < 15 for ex, ey in existing):
                continue
            # Crop the element from zoomed image for OCR
            cx1c = max(0, bx - pad)
            cy1c = max(0, by - pad)
            cx2c = min(zoomed_arr.shape[1], bx + bw + pad)
            cy2c = min(zoomed_arr.shape[0], by + bh + pad)
            btn_crop = zoomed_arr[cy1c:cy2c, cx1c:cx2c]
            if np.median(btn_crop) < 128:
                btn_crop = 255 - btn_crop
            btn_img = Image.fromarray(btn_crop)
            # Already zoomed, just scale 2x more for OCR
            btn_big = btn_img.resize((btn_img.width * 2, btn_img.height * 2),
                                     Image.LANCZOS)
            text = pytesseract.image_to_string(
                btn_big, lang="eng", config="--psm 10").strip()
            label = text if text else "[icon]"
            elements.append({
                "text": label,
                "x": x1 + scr_bx, "y": y1 + scr_by,
                "w": scr_bw, "h": scr_bh,
                "cx": bcx, "cy": bcy,
                "conf": 50 if text else 10,
            })
            existing.add((bcx, bcy))

        # Filter elements by square bounding box (not circle -- circle clips corners)
        elements = [e for e in elements
                    if abs(e["cx"] - x) <= radius and abs(e["cy"] - y) <= radius]


        # Save magnifier image (zoomed crop with OCR debug boxes)
        magnifier_path = f"/tmp/.magnifier-{self.desktop_id}.jpg"
        # Use the color crop (not grayscale) for the magnifier
        mag = crop.resize((crop.width * zoom, crop.height * zoom), Image.LANCZOS)

        # Draw OCR bounding boxes on magnifier for debug
        if elements:
            from PIL import ImageDraw
            draw = ImageDraw.Draw(mag)
            for el in elements:
                # Convert absolute screen coords to magnifier-local coords
                lx = (el["x"] - x1) * zoom
                ly = (el["y"] - y1) * zoom
                lw = el["w"] * zoom
                lh = el["h"] * zoom
                # Box color based on confidence
                color = "#00ff00" if el["conf"] >= 80 else "#ffff00" if el["conf"] >= 50 else "#ff4444"
                draw.rectangle([lx, ly, lx + lw, ly + lh], outline=color, width=2)
                # Label with text and confidence
                label = f'{el["text"]} ({el["conf"]})'
                try:
                    draw.text((lx, max(0, ly - 14)), label, fill=color)
                except Exception:
                    pass
            # Draw crosshair at cursor position
            cx_local = (x - x1) * zoom
            cy_local = (y - y1) * zoom
            ch_size = 10
            draw.line([cx_local - ch_size, cy_local, cx_local + ch_size, cy_local], fill="#ff00ff", width=2)
            draw.line([cx_local, cy_local - ch_size, cx_local, cy_local + ch_size], fill="#ff00ff", width=2)

        mag_buf = _io.BytesIO()
        mag.save(mag_buf, format="JPEG", quality=85)
        with open(magnifier_path, "wb") as f:
            f.write(mag_buf.getvalue())

        # Persist results to web-accessible debug dir (if configured)
        persist_dir = _os.environ.get("SCREENBOX_DEBUG_DIR", "")
        if persist_dir:
            _os.makedirs(persist_dir, exist_ok=True)
            mag.save(f"{persist_dir}/ocr-result.jpg", quality=90)
            crop.save(f"{persist_dir}/ocr-crop.jpg", quality=95)
            import json as _json
            with open(f"{persist_dir}/ocr-result.json", "w") as f:
                _json.dump({"x": x, "y": y, "radius": radius, "zoom": zoom,
                            "elements": elements, "count": len(elements)}, f, indent=2)

        # Write OCR boxes to container for click-indicator overlay
        if elements:
            try:
                import base64 as _b64
                ocr_json = json.dumps(elements)
                b64 = _b64.b64encode(ocr_json.encode()).decode()
                # b64 is safe (base64 charset only), but quote for defense-in-depth
                self._exec(["sh", "-c", f"printf '%s' {_shlex_quote(b64)} | base64 -d > /tmp/.ocr-boxes"],
                           timeout=2)
            except Exception:
                pass

        # Write gaze event for soft flash overlay
        try:
            gaze_data = f"{int(x)},{int(y)},{int(radius)}"
            self._exec(["sh", "-c", f"printf '%s' {_shlex_quote(gaze_data)} > /tmp/.gaze-event"],
                       timeout=2)
        except Exception:
            pass

        # Compute which grid cells the circle overlaps (10 cols x 6 rows default)
        screen_w, screen_h = 1920, 1080
        cols, rows = 10, 6
        cell_w, cell_h = screen_w / cols, screen_h / rows
        overlapped_cells = []
        for row in range(rows):
            for col in range(cols):
                # Cell center
                ccx = col * cell_w + cell_w / 2
                ccy = row * cell_h + cell_h / 2
                # Distance from cursor to cell center
                dist = ((ccx - x) ** 2 + (ccy - y) ** 2) ** 0.5
                # Circle overlaps cell if distance < radius + half-diagonal of cell
                half_diag = (cell_w ** 2 + cell_h ** 2) ** 0.5 / 2
                if dist < radius + half_diag:
                    overlapped_cells.append(row * cols + col + 1)

        return {
            "elements": elements,
            "count": len(elements),
            "viewport": [x1, y1, crop_w, crop_h],
            "center": [x, y],
            "radius": radius,
            "zoom": zoom,
            "magnifier": magnifier_path,
            "overlapped_cells": overlapped_cells,
        }
