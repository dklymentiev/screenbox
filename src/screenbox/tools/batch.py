"""desktop_batch tool."""
import json
import re
import time
from ..desktop import DISPLAY


def _shquote(s: str) -> str:
    """Shell-escape a string."""
    return "'" + s.replace("'", "'\\''") + "'"


def _action_to_bash(step: dict) -> str | None:
    """Convert a batch action to a bash command string. Returns None for unsupported actions."""
    action = step.get("action", "")
    if action == "click":
        x, y, btn = int(step["x"]), int(step["y"]), int(step.get("button", 1))
        return f"xdotool mousemove {x} {y} && xdotool mousedown --clearmodifiers {btn} && sleep 0.04 && xdotool mouseup --clearmodifiers {btn}"
    elif action == "double_click":
        x, y = int(step["x"]), int(step["y"])
        return f"xdotool mousemove {x} {y} && xdotool click --repeat 2 --delay 50 --clearmodifiers 1"
    elif action == "right_click":
        x, y = int(step["x"]), int(step["y"])
        return f"xdotool mousemove {x} {y} && xdotool mousedown --clearmodifiers 3 && sleep 0.04 && xdotool mouseup --clearmodifiers 3"
    elif action == "type":
        text = step.get("text", "")
        return f"xdotool type --clearmodifiers -- {_shquote(text)}"
    elif action == "key":
        combo = step.get("combo", "")
        if not re.match(r'^[a-zA-Z0-9+_\\-]+$', combo):
            return None
        return f"xdotool key --clearmodifiers {combo}"
    elif action == "scroll":
        x, y = int(step.get("x", 0)), int(step.get("y", 0))
        direction = step.get("direction", "down")
        amount = int(step.get("amount", 3))
        btn = 5 if direction == "down" else 4
        move = f"xdotool mousemove {x} {y} && " if x or y else ""
        return move + " && ".join([f"xdotool click {btn}"] * amount)
    elif action == "drag":
        x1, y1 = int(step["x1"]), int(step["y1"])
        x2, y2 = int(step["x2"]), int(step["y2"])
        btn = int(step.get("button", 1))
        return (f"xdotool mousemove {x1} {y1} && xdotool mousedown {btn} && sleep 0.1 "
                f"&& xdotool mousemove --sync {x2} {y2} && sleep 0.04 && xdotool mouseup {btn}")
    elif action == "modifier_click":
        x, y = int(step["x"]), int(step["y"])
        btn = int(step.get("button", 1))
        modifiers = step.get("modifiers", ["shift"])
        if not isinstance(modifiers, list):
            modifiers = [modifiers]
        mod_map = {"ctrl": "Control_L", "shift": "Shift_L", "alt": "Alt_L", "super": "Super_L"}
        keys = [mod_map.get(m, m) for m in modifiers]
        keydowns = " && ".join(f"xdotool keydown {k}" for k in keys)
        keyups = " && ".join(f"xdotool keyup {k}" for k in keys)
        return (f"{keydowns} && sleep 0.1 && xdotool mousemove {x} {y} "
                f"&& sleep 0.1 && xdotool click {btn} "
                f"&& sleep 0.1 && {keyups}")
    elif action == "move":
        return f"xdotool mousemove {int(step['x'])} {int(step['y'])}"
    elif action == "sleep":
        ms = int(step.get("ms", 200))
        return f"sleep {ms / 1000:.3f}"
    return None


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    def _batch_screenshot(d, a):
        """Take screenshot mid-batch, return base64 data."""
        return d.screenshot_base64(quality=a.get("quality", 50))

    _batch_fallback = {
        "navigate":    lambda d, a: d.navigate(a["url"]),
        "hover":       lambda d, a: d.hover(a["x"], a["y"], a.get("duration_ms", 0)),
        "screenshot":  _batch_screenshot,
    }

    @mcp.tool()
    def desktop_batch(desktop_id: str, actions: str,
                      intent: str = "",
                      step: str = "") -> str:
        """Execute a sequence of actions in one call. Much faster than separate tool calls.

        Combines xdotool actions into a single docker exec for speed.
        Each action is {"action": "<name>", ...params}.

        Supported actions:
          click:           {"action":"click", "x":100, "y":200, "button":1}
          double_click:    {"action":"double_click", "x":100, "y":200}
          right_click:     {"action":"right_click", "x":100, "y":200}
          drag:            {"action":"drag", "x1":100, "y1":200, "x2":300, "y2":400}
          modifier_click:  {"action":"modifier_click", "x":100, "y":200, "modifiers":["shift"]}
          type:            {"action":"type", "text":"hello"}
          key:             {"action":"key", "combo":"ctrl+a"}
          scroll:          {"action":"scroll", "x":100, "y":200, "direction":"down", "amount":3}
          move:            {"action":"move", "x":100, "y":200}
          hover:           {"action":"hover", "x":100, "y":200, "duration_ms":500}
          sleep:           {"action":"sleep", "ms":200}
          navigate:        {"action":"navigate", "url":"https://example.com"}
          screenshot:      {"action":"screenshot"} (returns base64 in result)

        Args:
            desktop_id: Desktop to run actions on
            actions: JSON array of action objects
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        steps = json.loads(actions) if isinstance(actions, str) else actions

        bash_parts = []
        fallback_after = []
        for i, s in enumerate(steps):
            bash_cmd = _action_to_bash(s)
            if bash_cmd:
                bash_parts.append(f"# step {i}: {s.get('action')}\n{bash_cmd}")
            else:
                action_name = s.get("action", "")
                if action_name in _batch_fallback:
                    fallback_after.append((i, s, list(bash_parts)))
                    bash_parts.clear()
                else:
                    return json.dumps({"error": f"Unknown action at step {i}: {action_name}"})

        results = []
        if bash_parts and not fallback_after:
            script = "set -e\n" + "\n".join(bash_parts)
            try:
                d._exec(["env", f"DISPLAY={DISPLAY}", "bash", "-c", script])
                for i, s in enumerate(steps):
                    results.append({"step": i, "action": s.get("action"), "ok": True})
            except Exception as e:
                results.append({"error": str(e)})
        else:
            step_idx = 0
            for fb_idx, fb_step, prior_bash in fallback_after:
                if prior_bash:
                    script = "set -e\n" + "\n".join(prior_bash)
                    d._exec(["env", f"DISPLAY={DISPLAY}", "bash", "-c", script])
                    while step_idx < fb_idx:
                        results.append({"step": step_idx, "action": steps[step_idx].get("action"), "ok": True})
                        step_idx += 1
                handler = _batch_fallback[fb_step.get("action")]
                rv = handler(d, fb_step)
                entry = {"step": fb_idx, "action": fb_step.get("action"), "ok": True}
                if rv is not None and isinstance(rv, str) and len(rv) < 200:
                    entry["result"] = rv
                elif rv is not None and isinstance(rv, str):
                    entry["result_length"] = len(rv)
                results.append(entry)
                step_idx = fb_idx + 1
            if bash_parts:
                script = "set -e\n" + "\n".join(bash_parts)
                d._exec(["env", f"DISPLAY={DISPLAY}", "bash", "-c", script])
                while step_idx < len(steps):
                    results.append({"step": step_idx, "action": steps[step_idx].get("action"), "ok": True})
                    step_idx += 1

        total_ms = int((time.time() - t0) * 1000)
        summary = {
            "steps_total": len(steps),
            "steps_done": len(results),
            "total_ms": total_ms,
            "results": results,
        }
        log_action(desktop_id, "desktop_batch",
                   {"action_count": len(steps), "actions": [s.get("action") for s in steps]},
                   summary, t0, intent=intent, step=step)
        return json.dumps(summary, indent=2)
