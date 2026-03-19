"""desktop_type, desktop_key, desktop_shell tools."""
import json
import time


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_type(desktop_id: str, text: str, delay: int = 0,
                     intent: str = "",
                     step: str = "") -> str:
        """Type text on the active window via keyboard.

        Args:
            desktop_id: Desktop to type on
            text: Text to type
            delay: Delay between keystrokes in ms (default 0)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        d.type_text(text, delay)
        result = {"typed": True, "length": len(text)}
        log_action(desktop_id, "desktop_type",
                   {"length": len(text)},
                   result, t0, intent=intent, step=step)
        return json.dumps(result)

    @mcp.tool()
    def desktop_key(desktop_id: str, keys: str, text: str = "",
                    intent: str = "",
                    step: str = "") -> str:
        """Press keyboard shortcut or key combination.

        Smart clipboard integration:
        - ctrl+c / ctrl+x: automatically returns clipboard content after copy/cut
        - ctrl+v with text param: sets clipboard first, then pastes (one action)

        Args:
            desktop_id: Desktop
            keys: Key combo (e.g. "ctrl+c", "Return", "Alt+F4", "Tab")
            text: For ctrl+v -- text to paste (sets clipboard before pressing ctrl+v)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        k = keys.lower().replace(" ", "")

        # Paste with text: set clipboard first, then Ctrl+V
        if text and k in ("ctrl+v", "ctrl+shift+v"):
            d.set_clipboard(text)
            d.key(keys)
            result = {"pressed": True, "keys": keys,
                      "pasted": True, "length": len(text)}
            log_action(desktop_id, "desktop_key",
                       {"keys": keys, "pasted": True},
                       result, t0, intent=intent, step=step)
            return json.dumps(result)

        d.key(keys)

        # Copy/Cut: return clipboard content automatically
        if k in ("ctrl+c", "ctrl+x"):
            import time as _t
            _t.sleep(0.05)  # tiny delay for clipboard to update
            try:
                content = d.get_clipboard()
                result = {"pressed": True, "keys": keys,
                          "clipboard": content}
            except Exception:
                result = {"pressed": True, "keys": keys,
                          "clipboard": None}
            log_action(desktop_id, "desktop_key",
                       {"keys": keys},
                       result, t0, intent=intent, step=step)
            return json.dumps(result)

        result = {"pressed": True, "keys": keys}
        log_action(desktop_id, "desktop_key",
                   {"keys": keys},
                   result, t0, intent=intent, step=step)
        return json.dumps(result)

    @mcp.tool()
    def desktop_shell(desktop_id: str, command: str, timeout: int = 30,
                      intent: str = "",
                      step: str = "") -> str:
        """Run shell command inside the desktop container.

        Args:
            desktop_id: Desktop
            command: Shell command to execute
            timeout: Max execution time in seconds (default 30)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        result = d.shell(command, timeout)
        # Truncate long output to prevent context pollution (e.g. wget progress)
        MAX_LINES = 50
        HEAD, TAIL = 30, 15
        for key in ("stdout", "stderr"):
            val = result.get(key, "")
            if not val:
                continue
            lines = val.split("\n")
            if len(lines) > MAX_LINES:
                omitted = len(lines) - HEAD - TAIL
                result[key] = "\n".join(
                    lines[:HEAD]
                    + [f"[... {omitted} lines truncated ...]"]
                    + lines[-TAIL:]
                )
        log_action(desktop_id, "desktop_shell", {"command": command}, result, t0, intent=intent, step=step)
        return json.dumps(result, indent=2)
