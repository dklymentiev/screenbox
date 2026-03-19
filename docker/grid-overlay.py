#!/usr/bin/env python3
"""
Grid Overlay - toggleable numbered grid on desktop.
Same pattern as click-indicator.py: transparent GTK3 window with X11 Shape Extension.

Control via file: write cols,rows to /tmp/.grid-overlay-config to show, delete to hide.
  echo "3,3" > /tmp/.grid-overlay-config   # show 3x3 grid
  echo "4,4" > /tmp/.grid-overlay-config   # switch to 4x4
  rm /tmp/.grid-overlay-config              # hide grid

Usage: DISPLAY=:99 python3 grid-overlay.py
Kill: kill $(cat /tmp/.grid-overlay-pid)
"""
import sys
import os
import signal
import cairo
import ctypes
import ctypes.util

os.environ.setdefault('DISPLAY', ':99')

import gi
gi.require_version('Gtk', '3.0')
from gi.repository import Gtk, Gdk, GLib

CONFIG_FILE = '/tmp/.grid-overlay-config'
PID_FILE = '/tmp/.grid-overlay-pid'
POLL_MS = 500  # check config file every 500ms


class X11ShapeHelper:
    """Persistent X11 connection for click-through via Shape Extension."""

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
                print("[grid-overlay] XOpenDisplay failed", flush=True)
        except Exception as e:
            print(f"[grid-overlay] X11 Shape init failed: {e}", flush=True)

    def clear_input_shape(self, gdk_window):
        """Set empty input shape -- clicks pass through."""
        if not self.dpy:
            return
        if self.xid is None:
            self.xid = gdk_window.get_xid()
        self.libXext.XShapeCombineRectangles(
            self.dpy, ctypes.c_ulong(self.xid),
            2, 0, 0, None, 0, 0, 0,
        )
        self.libX11.XFlush(self.dpy)


class GridOverlay(Gtk.Window):
    def __init__(self):
        super().__init__(title="Grid Overlay")

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

        self.cols = 0
        self.rows = 0
        self.visible_grid = False
        self._suspended = False  # SIGUSR1 suspends (hide), SIGUSR2 resumes (show)
        self._shape = X11ShapeHelper()

        self.connect("draw", self.on_draw)
        self.connect("destroy", Gtk.main_quit)

        # Poll config file
        GLib.timeout_add(POLL_MS, self._poll_config)

    def suspend(self):
        """Hide overlay instantly (for clean screenshots)."""
        self._suspended = True
        self.hide()

    def resume(self):
        """Show overlay instantly after screenshot."""
        self._suspended = False
        if self.visible_grid:
            self.show()
            self.queue_draw()

    def _poll_config(self):
        try:
            with open(CONFIG_FILE, 'r') as f:
                parts = f.read().strip().split(',')
                cols = int(parts[0])
                rows = int(parts[1]) if len(parts) > 1 else cols
                if cols != self.cols or rows != self.rows or not self.visible_grid:
                    self.cols = max(1, cols)
                    self.rows = max(1, rows)
                    self.visible_grid = True
                    if not self._suspended:
                        self.show_all()
                    self.queue_draw()
        except FileNotFoundError:
            if self.visible_grid:
                self.visible_grid = False
                self.hide()
        except (ValueError, IndexError):
            pass
        return True

    def on_draw(self, widget, cr):
        # Transparent background
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        if not self.visible_grid or self.cols < 1 or self.rows < 1:
            self._shape.clear_input_shape(self.get_window())
            return False

        w, h = self.sw, self.sh
        cw = w / self.cols
        ch = h / self.rows

        # Draw grid lines
        cr.set_source_rgba(1.0, 0.0, 0.0, 0.6)
        cr.set_line_width(2)

        for c in range(1, self.cols):
            x = c * cw
            cr.move_to(x, 0)
            cr.line_to(x, h)
            cr.stroke()

        for r in range(1, self.rows):
            y = r * ch
            cr.move_to(0, y)
            cr.line_to(w, y)
            cr.stroke()

        # Draw cell numbers
        cr.select_font_face("DejaVu Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(22)

        for r in range(self.rows):
            for c in range(self.cols):
                idx = r * self.cols + c + 1
                label = str(idx)
                x1 = c * cw
                y1 = r * ch

                extents = cr.text_extents(label)
                tx = x1 + 10
                ty = y1 + 10 + extents.height

                # Black background for label
                pad = 4
                cr.rectangle(tx - pad, ty - extents.height - pad,
                             extents.width + pad * 2, extents.height + pad * 2)
                cr.set_source_rgba(0, 0, 0, 0.7)
                cr.fill()

                # Green text
                cr.set_source_rgba(0.0, 1.0, 0.0, 0.9)
                cr.move_to(tx, ty)
                cr.show_text(label)

        # Click-through
        self._shape.clear_input_shape(self.get_window())
        return False


def main():
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

    def on_signal(sig, frame):
        Gtk.main_quit()
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    win = GridOverlay()
    # Do NOT show_all() here -- window starts hidden.
    # Shown only when grid config file exists (in _poll_config).
    # Without compositor, a "transparent" fullscreen window renders as opaque black.

    # SIGUSR1 = suspend (hide for clean screenshot), SIGUSR2 = resume
    def on_suspend(sig, frame):
        GLib.idle_add(win.suspend)
    def on_resume(sig, frame):
        GLib.idle_add(win.resume)
    signal.signal(signal.SIGUSR1, on_suspend)
    signal.signal(signal.SIGUSR2, on_resume)

    print(f"[grid-overlay] Running on {win.sw}x{win.sh}, polling {CONFIG_FILE}", flush=True)
    print(f"[grid-overlay] SIGUSR1=hide, SIGUSR2=show (PID {os.getpid()})", flush=True)
    Gtk.main()


if __name__ == '__main__':
    main()
