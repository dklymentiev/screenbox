"""Input/interaction mixin for Desktop class.

Mouse, keyboard, clipboard, window management, and overlay control methods.
All methods use self._exec(), self._xdotool(), self._log() etc. from the core Desktop class.
"""

import time
import logging

log = logging.getLogger("screenbox.desktop")

DISPLAY = ":99"


class InputMixin:
    """Mouse, keyboard, clipboard, window management, and overlay methods."""

    def screenshot_square(self, size: int = 1000, quality: int = 80) -> bytes:
        """Capture screen as 1000x1000 JPEG with cursor marker and info panel.

        Top: scaled desktop screenshot with bright cursor crosshair.
        Bottom: black info panel with cursor position and screen dimensions.
        """
        import io as _io
        from PIL import Image, ImageDraw

        raw = self.screenshot(quality=95)
        if not raw:
            return b""

        img = Image.open(_io.BytesIO(raw))
        real_w, real_h = img.size  # e.g. 1920x1080

        # Get cursor position
        cursor = self.get_cursor_position()
        cur_x = cursor.get("X", 0)
        cur_y = cursor.get("Y", 0)

        # Scale to fit width=size, keep aspect ratio
        scale = size / real_w
        scaled_h = int(real_h * scale)
        img_scaled = img.resize((size, scaled_h), Image.LANCZOS)

        # Create square canvas with black bottom panel
        canvas = Image.new("RGB", (size, size), (10, 14, 20))
        canvas.paste(img_scaled, (0, 0))

        draw = ImageDraw.Draw(canvas)

        # -- Draw cursor crosshair on screenshot --
        ccx = int(cur_x * scale)
        ccy = int(cur_y * scale)
        cursor_color = (255, 50, 50)  # bright red
        arm = 20
        # Crosshair lines
        draw.line([ccx - arm, ccy, ccx + arm, ccy], fill=cursor_color, width=2)
        draw.line([ccx, ccy - arm, ccx, ccy + arm], fill=cursor_color, width=2)
        # Circle around cursor
        r = 12
        draw.ellipse([ccx - r, ccy - r, ccx + r, ccy + r], outline=cursor_color, width=2)
        # Label with real coordinates
        label = f"({cur_x}, {cur_y})"
        draw.text((ccx + 16, ccy - 8), label, fill=(255, 255, 100))

        # -- Info panel in black area below screenshot --
        panel_y = scaled_h + 10
        info_color = (160, 170, 190)
        draw.text((10, panel_y),
                  f"Cursor: ({cur_x}, {cur_y})   Screen: {real_w}x{real_h}   "
                  f"Use hover_percent(dx%, dy%) to move cursor relatively",
                  fill=info_color)

        buf = _io.BytesIO()
        canvas.save(buf, format="JPEG", quality=quality)
        return buf.getvalue()

    def hover_percent(self, dx_pct: float, dy_pct: float) -> dict:
        """Move cursor by percentage of screen size from current position.

        dx_pct: horizontal shift (positive=right, negative=left), e.g. +10 = 10% right
        dy_pct: vertical shift (positive=down, negative=up), e.g. -5 = 5% up

        Returns new cursor position.
        """
        cursor = self.get_cursor_position()
        cur_x = cursor.get("X", 0)
        cur_y = cursor.get("Y", 0)

        # Get screen dimensions
        raw = self._exec(["env", f"DISPLAY={DISPLAY}", "xdpyinfo"],
                         timeout=3)
        screen_w, screen_h = 1920, 1080  # defaults
        for line in raw.split("\n"):
            if "dimensions:" in line:
                parts = line.split("dimensions:")[1].strip().split()[0]
                screen_w, screen_h = map(int, parts.split("x"))
                break

        new_x = int(cur_x + dx_pct / 100.0 * screen_w)
        new_y = int(cur_y + dy_pct / 100.0 * screen_h)

        # Clamp to screen
        new_x = max(0, min(new_x, screen_w - 1))
        new_y = max(0, min(new_y, screen_h - 1))

        self._xdotool("mousemove", str(new_x), str(new_y))

        return {
            "moved": True,
            "from": {"x": cur_x, "y": cur_y},
            "to": {"x": new_x, "y": new_y},
            "delta_pct": {"dx": dx_pct, "dy": dy_pct},
            "screen": {"w": screen_w, "h": screen_h},
        }

    def click(self, x: int, y: int, button: int = 1) -> None:
        """Click at (x, y). button: 1=left, 2=middle, 3=right.
        Activates window under cursor first (so click isn't consumed by focus change).
        Writes signal file for click-indicator overlay (yellow dot)."""
        t0 = time.time()
        # Cast to int to prevent injection via non-int values
        x, y, button = int(x), int(y), int(button)
        self._exec(["env", f"DISPLAY={DISPLAY}", "bash", "-c",
            f"echo '{x},{y}' > /tmp/.click-event && "
            f"xdotool mousemove {x} {y} && "
            f"WID=$(xdotool getmouselocation --shell | grep WINDOW | cut -d= -f2) && "
            f"xdotool windowactivate $WID 2>/dev/null; sleep 0.05; "
            f"xdotool mousedown --clearmodifiers {button} && sleep 0.04 && xdotool mouseup --clearmodifiers {button}"])
        self._log("click", {"x": x, "y": y, "button": button}, start_time=t0)

    def double_click(self, x: int, y: int) -> None:
        """Double-click at (x, y)."""
        t0 = time.time()
        self._xdotool("mousemove", str(x), str(y))
        self._xdotool("click", "--repeat", "2", "--delay", "50", "--clearmodifiers", "1")
        self._log("double_click", {"x": x, "y": y}, start_time=t0)

    def mouse_move(self, x: int, y: int) -> None:
        """Move mouse to (x, y)."""
        self._xdotool("mousemove", str(x), str(y))

    def mouse_down(self, x: int, y: int, button: int = 1) -> None:
        """Press mouse button at (x, y) without releasing."""
        self._xdotool("mousemove", str(x), str(y))
        self._xdotool("mousedown", str(button))

    def mouse_up(self, x: int, y: int, button: int = 1) -> None:
        """Release mouse button at (x, y)."""
        self._xdotool("mousemove", str(x), str(y))
        self._xdotool("mouseup", str(button))

    # -- Keyboard (xdotool -- primary for typing) --

    def _ensure_focus(self) -> None:
        """Activate the window under the current cursor position.

        This ensures keyboard input goes to the expected window,
        mirroring what click() does before mouse events.
        """
        try:
            self._exec(["env", f"DISPLAY={DISPLAY}", "bash", "-c",
                "WID=$(xdotool getmouselocation --shell | grep WINDOW | cut -d= -f2) && "
                "xdotool windowactivate $WID 2>/dev/null; sleep 0.05"])
        except Exception:
            pass  # best-effort: don't fail keyboard ops if focus fails

    def type_text(self, text: str, delay: int = 0) -> None:
        """Type text string. delay in ms between keystrokes."""
        t0 = time.time()
        self._ensure_focus()
        cmd = ["env", "DISPLAY=" + DISPLAY, "xdotool", "type", "--clearmodifiers"]
        if delay > 0:
            cmd.extend(["--delay", str(delay)])
        cmd.append(text)
        self._exec(cmd)
        self._log("type_text", {"text": text, "length": len(text)}, start_time=t0)

    def key(self, combo: str) -> None:
        """Press key combination. E.g. 'ctrl+c', 'Return', 'alt+F4'."""
        t0 = time.time()
        self._ensure_focus()
        self._xdotool("key", "--clearmodifiers", combo)
        self._log("key", {"combo": combo}, start_time=t0)

    # -- Scroll --

    def scroll(self, direction: str = "down", amount: int = 3) -> None:
        """Scroll. direction: up/down/left/right.
        Activates window under cursor first (same as click)."""
        t0 = time.time()
        button_map = {"up": "4", "down": "5", "left": "6", "right": "7"}
        button = button_map.get(direction, "5")
        capped = min(amount, 20)
        self._exec(["env", f"DISPLAY={DISPLAY}", "bash", "-c",
            f"WID=$(xdotool getmouselocation --shell | grep WINDOW | cut -d= -f2) && "
            f"xdotool windowactivate $WID 2>/dev/null; sleep 0.05; "
            f"xdotool click --repeat {capped} --delay 30 {button}"])
        self._log("scroll", {"direction": direction, "amount": amount}, start_time=t0)

    # -- Drag & right-click --

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: int = 1) -> None:
        """Drag from (x1,y1) to (x2,y2).
        Uses separate exec calls so WM has time to process each event."""
        t0 = time.time()
        self._xdotool("mousemove", str(x1), str(y1))
        self._exec(["env", f"DISPLAY={DISPLAY}", "xdotool",
                     "mousedown", "--clearmodifiers", str(button)])
        self._exec(["sleep", "0.15"])
        self._xdotool("mousemove", "--sync", str(x2), str(y2))
        self._exec(["env", f"DISPLAY={DISPLAY}", "xdotool",
                     "mouseup", "--clearmodifiers", str(button)])
        self._log("drag", {"x1": x1, "y1": y1, "x2": x2, "y2": y2}, start_time=t0)

    def right_click(self, x: int, y: int) -> None:
        """Right-click at (x, y)."""
        self.click(x, y, button=3)

    def hover(self, x: int, y: int, duration_ms: int = 0) -> None:
        """Move mouse to (x, y) and optionally wait."""
        self._xdotool("mousemove", str(x), str(y))
        if duration_ms > 0:
            self._exec(["sleep", str(duration_ms / 1000.0)])

    # -- Clipboard --

    def get_clipboard(self) -> str:
        """Get clipboard content."""
        return self._exec(["env", "DISPLAY=" + DISPLAY, "xclip",
                           "-selection", "clipboard", "-o"]).strip()

    def set_clipboard(self, text: str) -> None:
        """Set clipboard content."""
        import shlex
        # Use shlex.quote (single-quote wrapping) to prevent shell injection.
        # json.dumps inside double quotes would allow $(cmd) and backtick execution.
        self._exec(
            ["bash", "-c",
             f"printf '%s' {shlex.quote(text)} | DISPLAY={DISPLAY} xclip -selection clipboard"],
        )

    # -- Window management --

    # Window names that are desktop environment internals, not user apps
    _SKIP_WINDOW_NAMES = {
        "Click Indicator", "Grid Overlay",
        "Desktop", "xfdesktop", "xfce4-panel",
    }
    _SKIP_WINDOW_SUBSTRINGS = [
        "xfce4-panel", "xfdesktop", "mate-panel",
        "wrapper-2.0", "xfce4-notifyd",
    ]

    def _is_app_window(self, name: str, width: int = 0, height: int = 0) -> bool:
        """Check if window is a real app window (not DE panel/desktop/overlay)."""
        if not name:
            return False
        if name in self._SKIP_WINDOW_NAMES:
            return False
        name_lower = name.lower()
        for sub in self._SKIP_WINDOW_SUBSTRINGS:
            if sub in name_lower:
                return False
        if width > 0 and height > 0 and (width <= 10 or height <= 10):
            return False
        return True

    def get_windows(self) -> list:
        """List application windows (filters out panels, desktop, overlays)."""
        out = self._exec(["env", "DISPLAY=" + DISPLAY, "wmctrl", "-lGp"])
        if not out.strip():
            # wmctrl not available, fallback to xdotool
            out = self._xdotool("search", "--onlyvisible", "--name", "")
            window_ids = [wid.strip() for wid in out.strip().split("\n") if wid.strip()]
            windows = []
            for wid in window_ids[:20]:
                name = self._xdotool("getwindowname", wid).strip()
                geo = self._xdotool("getwindowgeometry", "--shell", wid)
                info = {"id": wid, "name": name}
                w, h = 0, 0
                for line in geo.strip().split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        info[k.strip().lower()] = v.strip()
                        if k.strip().lower() == "width":
                            w = int(v.strip())
                        elif k.strip().lower() == "height":
                            h = int(v.strip())
                if self._is_app_window(name, w, h):
                    windows.append(info)
            return windows
        # Parse wmctrl -lGp output: ID DESK PID X Y W H HOST NAME
        windows = []
        for line in out.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split(None, 8)
            if len(parts) >= 9:
                w, h = int(parts[5]), int(parts[6])
                name = parts[8]
                # desktop=-1 means sticky/always-on-top (panels, desktop)
                if parts[1] == "-1":
                    continue
                if not self._is_app_window(name, w, h):
                    continue
                windows.append({
                    "id": parts[0],
                    "pid": parts[2],
                    "name": name,
                    "x": int(parts[3]), "y": int(parts[4]),
                    "width": w, "height": h,
                })
        return windows

    def activate_window(self, window_id: str = None) -> None:
        """Activate (focus) a window by ID. Uses active window if no ID given."""
        if not window_id:
            window_id = self._xdotool("getactivewindow").strip()
        if window_id:
            self._xdotool("windowactivate", window_id)

    def minimize_window(self, window_id: str = None) -> None:
        """Minimize a window. Uses active window if no ID given."""
        if not window_id:
            window_id = self._xdotool("getactivewindow").strip()
        if window_id:
            self._xdotool("windowminimize", window_id)

    def get_windows_near(self, x: int, y: int, radius: int = 500) -> list:
        """Get windows overlapping a circle around (x, y) with z-order.

        Uses _NET_CLIENT_LIST_STACKING for proper z-index (bottom-to-top order).
        Returns windows that geometrically intersect the (x, y, radius) area.
        """
        # Get stacking order from root window property
        try:
            raw = self._exec([
                "env", f"DISPLAY={DISPLAY}",
                "xprop", "-root", "_NET_CLIENT_LIST_STACKING"
            ]).strip()
            # Parse: "_NET_CLIENT_LIST_STACKING(WINDOW): window id # 0x..., 0x..., ..."
            if "window id #" in raw:
                hex_ids = raw.split("window id #")[1].strip()
                stacking = [wid.strip().rstrip(",") for wid in hex_ids.split(",") if wid.strip()]
            else:
                stacking = []
        except Exception:
            stacking = []

        # Get all visible windows with geometry
        all_windows = []
        try:
            out = self._xdotool("search", "--onlyvisible", "--name", "")
            window_ids = [wid.strip() for wid in out.strip().split("\n") if wid.strip()]
        except Exception:
            window_ids = []

        # Build z-index map from stacking order
        z_map = {}
        for i, wid_hex in enumerate(stacking):
            try:
                z_map[int(wid_hex, 16)] = i
            except ValueError:
                pass

        for wid in window_ids[:30]:
            try:
                name = self._xdotool("getwindowname", wid).strip()
                if not name:
                    continue
                geo = self._xdotool("getwindowgeometry", "--shell", wid)
                info = {"id": wid, "name": name}
                for line in geo.strip().split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        info[k.strip().lower()] = v.strip()

                # Parse geometry
                wx = int(info.get("x", 0))
                wy = int(info.get("y", 0))
                ww = int(info.get("width", 0))
                wh = int(info.get("height", 0))

                if not self._is_app_window(name, ww, wh):
                    continue

                # Check intersection: circle (x, y, radius) vs rect (wx, wy, ww, wh)
                closest_x = max(wx, min(x, wx + ww))
                closest_y = max(wy, min(y, wy + wh))
                dist_sq = (x - closest_x) ** 2 + (y - closest_y) ** 2
                if dist_sq <= radius * radius:
                    wid_int = int(wid)
                    info["z_index"] = z_map.get(wid_int, -1)
                    info["x"] = wx
                    info["y"] = wy
                    info["width"] = ww
                    info["height"] = wh
                    all_windows.append(info)
            except Exception:
                continue

        # Sort by z_index (top window last = highest z)
        all_windows.sort(key=lambda w: w.get("z_index", -1))
        return all_windows

    def maximize_window(self) -> None:
        """Maximize the active window via key combo."""
        self._xdotool("key", "super+Up")

    def close_window(self, window_id: str = None) -> None:
        """Close a window."""
        if window_id:
            self._xdotool("windowclose", window_id)
        else:
            self._xdotool("key", "alt+F4")

    def window_move(self, x: int, y: int, window_id: str = None) -> None:
        """Move a window. If no window_id, moves active window."""
        if window_id:
            self._xdotool("windowmove", window_id, str(x), str(y))
        else:
            self._xdotool("getactivewindow", "windowmove", str(x), str(y))

    def window_restore(self, window_id: str = None) -> None:
        """Restore minimized/maximized window to normal size."""
        if window_id:
            self._xdotool("windowactivate", window_id)
        # Toggle maximize to restore
        self._xdotool("key", "super+Down")

    def show_desktop(self) -> None:
        """Minimize all windows."""
        self._xdotool("key", "super+d")

    def window_resize(self, width: int, height: int, window_id: str = None) -> None:
        """Resize a window. If no window_id, resizes active window."""
        if window_id:
            self._xdotool("windowsize", window_id, str(width), str(height))
        else:
            self._xdotool("getactivewindow", "windowsize", str(width), str(height))

    # -- Overlay --

    def overlay_mode(self, enabled: bool = True, cursor: bool = True,
                     dots: bool = True, trail: bool = True) -> dict:
        """Set click indicator display mode.

        Args:
            enabled: Master switch. False = hide overlay, stop all timers (0% CPU).
            cursor: Show cursor arrow overlay.
            dots: Show yellow click dots.
            trail: Show mouse trail (comet effect).
        """
        config = (f"enabled={'1' if enabled else '0'}\n"
                  f"cursor={'1' if cursor else '0'}\n"
                  f"dots={'1' if dots else '0'}\n"
                  f"trail={'1' if trail else '0'}")
        # Write config without shell interpolation -- printf with %s is injection-safe
        self._exec(["sh", "-c", "printf '%s' \"$1\" > /tmp/.click-indicator-config", "sh", config])
        return {"enabled": enabled, "cursor": cursor, "dots": dots, "trail": trail}
