#!/usr/bin/env python3
"""
Click Indicator + Cursor - configurable overlay for desktop interaction.

Features: click dots, mouse trail (comet), cursor arrow, OCR boxes, gaze flash.
Each feature toggleable via config file /tmp/.click-indicator-config.

Config format (one setting per line):
  enabled=1    (master switch, default: ON)
  cursor=1     (show cursor arrow, default: OFF)
  dots=1       (show click dots, default: ON)
  trail=0      (show mouse trail/comet, default: OFF)
  gaze=1       (show gaze flash on look, default: ON)

Write config: echo 'enabled=1\ncursor=1\ndots=1\ntrail=0' > /tmp/.click-indicator-config

CPU-efficient: event-driven architecture.
- Idle: ~0% CPU (no timers running)
- Active animation: 60Hz only while dots/trail/OCR are fading
- Click detection: watches /tmp/.click-event via inotify + GDK button mask at 4Hz

Uses X11 Shape Extension (via ctypes) for click-through on Xvfb.

Usage: DISPLAY=:99 python3 click-indicator.py
Kill: kill $(cat /tmp/.click-indicator-pid)
"""
import sys
import os
import signal
import cairo
import time
import math
import json
import ctypes
import ctypes.util

os.environ.setdefault('DISPLAY', ':99')

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib, Gio

FADE_SECS = 0.8
RADIUS = 20
CLICK_EVENT_FILE = '/tmp/.click-event'
CONFIG_FILE = '/tmp/.click-indicator-config'
OCR_BOXES_FILE = '/tmp/.ocr-boxes'
GAZE_EVENT_FILE = '/tmp/.gaze-event'
TRAIL_LENGTH = 30
OCR_FADE_SECS = 3.0
GAZE_FADE_SECS = 1.2

# Tick intervals (ms)
TICK_ANIMATE = 16   # ~60Hz when animating
TICK_IDLE = 250     # 4Hz when idle (just poll mouse/clicks)


class X11ShapeHelper:
    """Persistent X11 connection for setting input shape every frame."""

    def __init__(self):
        self.dpy = None
        self.xid = None
        self._setup_libs()

    def _setup_libs(self):
        try:
            x11_path = ctypes.util.find_library('X11') or 'libX11.so.6'
            xext_path = ctypes.util.find_library('Xext') or 'libXext.so.6'

            self.libX11 = ctypes.cdll.LoadLibrary(x11_path)
            self.libXext = ctypes.cdll.LoadLibrary(xext_path)

            self.libX11.XOpenDisplay.restype = ctypes.c_void_p
            self.libX11.XOpenDisplay.argtypes = [ctypes.c_char_p]
            self.libX11.XFlush.restype = ctypes.c_int
            self.libX11.XFlush.argtypes = [ctypes.c_void_p]

            self.libXext.XShapeCombineRectangles.restype = None
            self.libXext.XShapeCombineRectangles.argtypes = [
                ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int,
                ctypes.c_int, ctypes.c_int, ctypes.c_void_p,
                ctypes.c_int, ctypes.c_int, ctypes.c_int,
            ]

            display_str = os.environ.get('DISPLAY', ':99').encode('ascii')
            self.dpy = self.libX11.XOpenDisplay(display_str)
            if not self.dpy:
                print("[click-indicator] XOpenDisplay failed", flush=True)
        except Exception as e:
            print(f"[click-indicator] X11 Shape init failed: {e}", flush=True)

    def clear_input_shape(self, gdk_window):
        """Set empty input shape -- call after every draw."""
        if not self.dpy:
            return
        if self.xid is None:
            self.xid = gdk_window.get_xid()
            print(f"[click-indicator] X11 Shape: xid={self.xid}", flush=True)
        # ShapeInput=2, ShapeSet=0
        self.libXext.XShapeCombineRectangles(
            self.dpy, ctypes.c_ulong(self.xid),
            2, 0, 0, None, 0, 0, 0,
        )
        self.libX11.XFlush(self.dpy)


class ClickOverlay(Gtk.Window):
    def __init__(self):
        super().__init__(title="Click Indicator")

        screen = self.get_screen()
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        self.set_app_paintable(True)
        self.set_decorated(False)
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_keep_above(True)
        self.set_accept_focus(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)

        display = Gdk.Display.get_default()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        geom = monitor.get_geometry()
        self.sw = geom.width
        self.sh = geom.height
        self.move(geom.x, geom.y)
        self.set_default_size(self.sw, self.sh)

        self.mx = 0
        self.my = 0
        self.dots = []   # [(x, y, timestamp)]
        self.trail = []  # [(x, y, timestamp)] recent mouse positions
        self._prev_pressed = False
        self._last_event_mtime = 0
        self._shape = X11ShapeHelper()

        # Display modes (dots+cursor on, trail off by default)
        self.enabled = True
        self.show_cursor = False
        self.show_dots = True
        self.show_trail = False
        self._config_mtime = 0

        # OCR overlay boxes
        self.ocr_boxes = []  # [(x, y, w, h, text, conf, timestamp)]
        self._ocr_mtime = 0

        # Gaze flash
        self.gazes = []  # [(x, y, radius, timestamp)]
        self._gaze_mtime = 0
        self.show_gaze = True
        self.show_ocr_boxes = False  # debug only, off by default

        # Adaptive tick state
        self._tick_id = None
        self._animating = False  # True when dots/trail/OCR are visible

        self.connect("draw", self.on_draw)
        self.connect("destroy", Gtk.main_quit)

        # Start idle tick (low frequency)
        self._schedule_tick(TICK_IDLE)
        # Check config every 2s (was 500ms -- no need for that)
        GLib.timeout_add(2000, self._check_config)
        # Watch click-event file via inotify if available, else poll in _tick
        self._setup_click_watch()

    def _setup_click_watch(self):
        """Try to watch click-event file via GLib file monitor (inotify)."""
        try:
            click_file = Gio.File.new_for_path(CLICK_EVENT_FILE)
            # Watch parent dir for file creation
            parent = Gio.File.new_for_path('/tmp')
            self._click_monitor = parent.monitor_directory(
                Gio.FileMonitorFlags.NONE, None)
            self._click_monitor.connect('changed', self._on_click_file_changed)
        except Exception:
            # Fallback: poll in _tick (already does this)
            self._click_monitor = None

    def _on_click_file_changed(self, monitor, file, other_file, event_type):
        """inotify callback when files change in /tmp."""
        if file.get_basename() == '.click-event':
            self._check_click_event_file()
            self._ensure_animating()
        elif file.get_basename() == '.ocr-boxes':
            self._check_ocr_boxes()
            self._ensure_animating()
        elif file.get_basename() == '.gaze-event':
            self._check_gaze_event()
            self._ensure_animating()

    def _check_config(self):
        """Read display mode config from file."""
        try:
            st = os.stat(CONFIG_FILE)
            if st.st_mtime == self._config_mtime:
                return True
            self._config_mtime = st.st_mtime
            with open(CONFIG_FILE, 'r') as f:
                raw = f.read()
            was_enabled = self.enabled
            for line in raw.strip().split('\n'):
                line = line.strip()
                if '=' not in line:
                    continue
                key, val = line.split('=', 1)
                key = key.strip()
                val = val.strip()
                if key == 'enabled':
                    self.enabled = val == '1'
                elif key == 'cursor':
                    self.show_cursor = val == '1'
                elif key == 'dots':
                    self.show_dots = val == '1'
                elif key == 'trail':
                    self.show_trail = val == '1'
                elif key == 'gaze':
                    self.show_gaze = val == '1'
                elif key == 'ocr':
                    self.show_ocr_boxes = val == '1'
            # Handle enable/disable transitions
            if self.enabled and not was_enabled:
                self._schedule_tick(TICK_IDLE)
                print("[click-indicator] ENABLED", flush=True)
            elif not self.enabled and was_enabled:
                self.hide()
                self._cancel_tick()
                self.dots.clear()
                self.trail.clear()
                self.ocr_boxes.clear()
                self.gazes.clear()
                self._animating = False
                print("[click-indicator] DISABLED (0% CPU)", flush=True)
            else:
                print(f"[click-indicator] Config: enabled={self.enabled} cursor={self.show_cursor} dots={self.show_dots} trail={self.show_trail}", flush=True)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[click-indicator] Config error: {e}", flush=True)
        return True

    def _check_ocr_boxes(self):
        """Read OCR bounding boxes from file (written by hover())."""
        try:
            st = os.stat(OCR_BOXES_FILE)
            if st.st_mtime == self._ocr_mtime:
                return
            self._ocr_mtime = st.st_mtime
            with open(OCR_BOXES_FILE, 'r') as f:
                data = json.loads(f.read())
            now = time.time()
            self.ocr_boxes = []
            for el in data:
                self.ocr_boxes.append((
                    el["x"], el["y"], el["w"], el["h"],
                    el.get("text", ""), el.get("conf", 0), now
                ))
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass
        except Exception as e:
            print(f"[click-indicator] OCR read error: {e}", flush=True)

    def _check_gaze_event(self):
        """Read gaze event from file (written by desktop_look)."""
        try:
            st = os.stat(GAZE_EVENT_FILE)
            if st.st_mtime == self._gaze_mtime:
                return
            self._gaze_mtime = st.st_mtime
            with open(GAZE_EVENT_FILE, 'r') as f:
                data = f.read().strip()
            if ',' in data:
                parts = data.split(',')
                gx, gy = int(parts[0]), int(parts[1])
                gr = int(parts[2]) if len(parts) > 2 else 200
                self.gazes.append((gx, gy, gr, time.time()))
        except (FileNotFoundError, ValueError, IndexError, OSError):
            pass

    def _check_click_event_file(self):
        """Check /tmp/.click-event for clicks from desktop.click() (xdotool)."""
        try:
            st = os.stat(CLICK_EVENT_FILE)
            mtime = st.st_mtime
            if mtime > self._last_event_mtime:
                self._last_event_mtime = mtime
                with open(CLICK_EVENT_FILE, 'r') as f:
                    data = f.read().strip()
                if ',' in data:
                    parts = data.split(',')
                    cx, cy = int(parts[0]), int(parts[1])
                    self.dots.append((cx, cy, time.time()))
        except (FileNotFoundError, ValueError, IndexError, OSError):
            pass

    def _schedule_tick(self, interval_ms):
        """Schedule next tick, cancelling any existing one."""
        self._cancel_tick()
        self._tick_id = GLib.timeout_add(interval_ms, self._tick)

    def _cancel_tick(self):
        """Cancel pending tick timer."""
        if self._tick_id is not None:
            GLib.source_remove(self._tick_id)
            self._tick_id = None

    def _ensure_animating(self):
        """Switch to fast tick if not already animating."""
        if not self._animating and self.enabled:
            self._animating = True
            self._schedule_tick(TICK_ANIMATE)

    def _has_active_visuals(self):
        """Check if there are any active animations that need rendering."""
        now = time.time()
        if self.dots:
            # Any non-expired dots?
            cutoff = now - FADE_SECS - 0.1
            if any(d[2] > cutoff for d in self.dots):
                return True
        if self.show_trail and len(self.trail) > 1:
            return True
        if self.ocr_boxes:
            if any(now - b[6] < OCR_FADE_SECS for b in self.ocr_boxes):
                return True
        if self.gazes:
            if any(now - g[3] < GAZE_FADE_SECS for g in self.gazes):
                return True
        return False

    def _tick(self):
        if not self.enabled:
            self._tick_id = None
            return False  # Stop timer

        now = time.time()

        # Poll mouse position
        display = Gdk.Display.get_default()
        seat = display.get_default_seat()
        pointer = seat.get_pointer()
        root = Gdk.get_default_root_window()
        _, mx, my, mask = root.get_device_position(pointer)

        # Detect new click via GDK button mask (VNC real mouse)
        pressed = bool(mask & (
            Gdk.ModifierType.BUTTON1_MASK |
            Gdk.ModifierType.BUTTON2_MASK |
            Gdk.ModifierType.BUTTON3_MASK
        ))
        if pressed and not self._prev_pressed:
            self.dots.append((mx, my, now))
            self._animating = True
        self._prev_pressed = pressed

        # Check file-based click events (xdotool via MCP)
        self._check_click_event_file()

        # Check OCR boxes and gaze events (in case inotify missed it)
        self._check_ocr_boxes()
        self._check_gaze_event()

        # Cleanup expired dots
        cutoff = now - FADE_SECS - 0.1
        self.dots = [d for d in self.dots if d[2] > cutoff]

        # Record trail point with timestamp (only if trail enabled)
        if self.show_trail:
            if not self.trail or (self.trail[-1][0] != mx or self.trail[-1][1] != my):
                self.trail.append((mx, my, now))
                self._animating = True
            trail_cutoff = now - 0.5
            self.trail = [t for t in self.trail if t[2] > trail_cutoff][-TRAIL_LENGTH:]

        # Track mouse movement
        mouse_moved = (mx != self.mx or my != self.my)
        self.mx = mx
        self.my = my

        # Decide next tick interval
        has_visuals = self._has_active_visuals()
        need_cursor_redraw = self.show_cursor and mouse_moved

        if has_visuals or need_cursor_redraw:
            # Show window and redraw (may be hidden when idle)
            if not self.get_visible():
                self.show_all()
            win = self.get_window()
            if win:
                win.invalidate_rect(None, True)
            self.queue_draw()

        if has_visuals:
            # Stay in fast mode
            if not self._animating:
                self._animating = True
            self._tick_id = GLib.timeout_add(TICK_ANIMATE, self._tick)
        else:
            # Drop to idle -- hide window so it doesn't block desktop
            # (without compositor, "transparent" fullscreen = opaque black)
            if self._animating:
                self._animating = False
            if self.get_visible() and not self.show_cursor:
                self.hide()
            self._tick_id = GLib.timeout_add(TICK_IDLE, self._tick)

        return False  # Don't repeat -- we reschedule manually

    def on_draw(self, widget, cr):
        # Transparent background
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        now = time.time()

        # Draw click dots
        if self.show_dots:
            for x, y, ts in self.dots:
                age = now - ts
                frac = min(age / FADE_SECS, 1.0)
                if frac >= 1.0:
                    continue

                alpha = 1.0 - frac
                expand = RADIUS + frac * 12

                # Cool blue-white dot (layered circles)
                for layer in range(3):
                    lr = expand * (1.0 - layer * 0.25)
                    la = alpha * (0.3 + layer * 0.15)
                    cr.arc(x, y, lr, 0, 2 * math.pi)
                    # Outer=blue, inner=white-blue
                    blend = layer / 2.0  # 0=outer, 1=inner
                    r = 0.4 + blend * 0.3   # 0.4 -> 0.7
                    g = 0.65 + blend * 0.2  # 0.65 -> 0.85
                    b = 1.0
                    cr.set_source_rgba(r, g, b, la)
                    cr.fill()

        # Draw mouse trail (yellow comet with fade)
        if self.show_trail:
            n = len(self.trail)
            if n > 1:
                for i in range(n - 1):
                    tx, ty, ts = self.trail[i]
                    age = now - ts
                    time_alpha = max(0, 1.0 - age / 0.5)  # fade over 0.5s
                    frac = i / n  # 0.0 = oldest, ~1.0 = newest
                    alpha = frac * 0.5 * time_alpha
                    if alpha < 0.01:
                        continue
                    r = 2 + frac * 6
                    cr.arc(tx, ty, r, 0, 2 * math.pi)
                    cr.set_source_rgba(0.5, 0.75, 1.0, alpha)
                    cr.fill()

        # Draw gaze flash (soft radial glow at look area)
        if self.show_gaze and self.gazes:
            alive_gazes = []
            for gx, gy, gr, ts in self.gazes:
                age = now - ts
                if age > GAZE_FADE_SECS:
                    continue
                alive_gazes.append((gx, gy, gr, ts))
                frac = age / GAZE_FADE_SECS
                # Ease-out: fast appear, slow fade
                alpha = (1.0 - frac ** 0.5) * 0.35
                # Expand slightly as it fades
                r = gr * (1.0 + frac * 0.15)
                # Radial gradient: cool blue-white center -> transparent edge
                pattern = cairo.RadialGradient(gx, gy, 0, gx, gy, r)
                pattern.add_color_stop_rgba(0.0, 0.7, 0.85, 1.0, alpha * 0.9)
                pattern.add_color_stop_rgba(0.3, 0.4, 0.65, 1.0, alpha * 0.4)
                pattern.add_color_stop_rgba(0.7, 0.2, 0.45, 0.9, alpha * 0.15)
                pattern.add_color_stop_rgba(1.0, 0.1, 0.3, 0.8, 0.0)
                cr.set_source(pattern)
                cr.arc(gx, gy, r, 0, 2 * math.pi)
                cr.fill()
            self.gazes = alive_gazes

        # Draw OCR bounding boxes (debug mode, off by default)
        if self.show_ocr_boxes and self.ocr_boxes:
            alive = []
            for bx, by, bw, bh, text, conf, ts in self.ocr_boxes:
                age = now - ts
                if age > OCR_FADE_SECS:
                    continue
                alive.append((bx, by, bw, bh, text, conf, ts))
                alpha = max(0, 1.0 - age / OCR_FADE_SECS) * 0.9

                # Color by confidence
                if conf >= 80:
                    r, g, b = 0.0, 1.0, 0.0   # green
                elif conf >= 50:
                    r, g, b = 1.0, 1.0, 0.0   # yellow
                else:
                    r, g, b = 1.0, 0.3, 0.3   # red

                # Box outline
                cr.set_source_rgba(r, g, b, alpha)
                cr.set_line_width(2)
                cr.rectangle(bx, by, bw, bh)
                cr.stroke()

                # Semi-transparent fill
                cr.set_source_rgba(r, g, b, alpha * 0.1)
                cr.rectangle(bx, by, bw, bh)
                cr.fill()

                # Label
                cr.set_source_rgba(r, g, b, alpha)
                cr.set_font_size(11)
                label = f"{text} ({conf})"
                cr.move_to(bx, max(10, by - 3))
                cr.show_text(label)

            self.ocr_boxes = alive

        # Draw cursor arrow
        if self.show_cursor:
            mx, my = self.mx, self.my
            if 0 <= mx < self.sw and 0 <= my < self.sh:
                cr.set_source_rgba(1, 1, 1, 0.95)
                cr.move_to(mx, my)
                cr.line_to(mx, my + 18)
                cr.line_to(mx + 5, my + 14)
                cr.line_to(mx + 12, my + 14)
                cr.close_path()
                cr.fill_preserve()
                cr.set_source_rgba(0, 0, 0, 1)
                cr.set_line_width(1.5)
                cr.stroke()

        # Re-apply empty input shape after every draw (GTK resets it)
        self._shape.clear_input_shape(self.get_window())

        return False


def main():
    with open('/tmp/.click-indicator-pid', 'w') as f:
        f.write(str(os.getpid()))

    def on_signal(sig, frame):
        Gtk.main_quit()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    win = ClickOverlay()
    # Start hidden -- show only when there are visuals to draw.
    # Without compositor, a fullscreen "transparent" window renders as opaque black.
    win.realize()  # Realize so GDK window exists for timers/events
    print(f"[click-indicator] Running on {win.sw}x{win.sh} (hidden until first visual)", flush=True)
    Gtk.main()


if __name__ == '__main__':
    main()
