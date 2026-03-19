"""desktop_window dispatcher tool."""
import json
import time


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_window(desktop_id: str, action: str,
                       window_id: str = "",
                       width: int = 0, height: int = 0,
                       x: int = 0, y: int = 0,
                       intent: str = "",
                       step: str = "") -> str:
        """Window management operations.

        Actions:
          list -- app windows only (panels/desktop/overlays filtered out)
          activate(window_id) | minimize(window_id) | maximize
          restore(window_id?) | resize(width, height, window_id?)
          move(x, y, window_id?) | close(window_id?) | show_desktop

        Args:
            desktop_id: Desktop
            action: Action name
            window_id: Window ID (from list action)
            width: Width for resize
            height: Height for resize
            x: X for move
            y: Y for move
        """
        t0 = time.time()
        d = get_desktop(desktop_id)

        if action == "list":
            result = d.get_windows()
            log_action(desktop_id, "desktop_window",
                       {"action": "list"}, f"{len(result)} windows", t0,
                       intent=intent, step=step)
            return json.dumps(result, indent=2)
        elif action == "activate":
            if not window_id:
                window_id = d._xdotool("getactivewindow").strip()
            d.activate_window(window_id)
            result = {"activated": True, "window_id": window_id}
        elif action == "minimize":
            if not window_id:
                window_id = d._xdotool("getactivewindow").strip()
            if not window_id:
                return json.dumps({"error": "No active window to minimize"})
            d.minimize_window(window_id)
            result = {"minimized": True, "window_id": window_id}
        elif action == "maximize":
            d.maximize_window()
            result = {"maximized": True}
        elif action == "restore":
            d.window_restore(window_id or None)
            result = {"restored": True, "window_id": window_id}
        elif action == "resize":
            d.window_resize(width, height, window_id or None)
            result = {"resized": True, "width": width, "height": height}
        elif action == "move":
            d.window_move(x, y, window_id or None)
            result = {"moved": True, "x": x, "y": y}
        elif action == "close":
            d.close_window(window_id or None)
            result = {"closed": True, "window_id": window_id}
        elif action == "show_desktop":
            d.show_desktop()
            result = {"ok": True}
        else:
            return json.dumps({"error": f"Unknown window action: {action}",
                               "available": [
                                   "list", "activate", "minimize", "maximize",
                                   "restore", "resize", "move", "close",
                                   "show_desktop"]})

        log_action(desktop_id, "desktop_window", {"action": action}, result, t0, intent=intent, step=step)
        return json.dumps(result)
