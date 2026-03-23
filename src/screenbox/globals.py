"""Screenbox global state and MCP server instance.

This module provides the shared state (manager, config, loggers) and the
FastMCP server instance with tool instructions. Tool handlers are registered
from the tools/ package at import time.
"""

import atexit
import json
import logging
import os
import re
import time
from mcp.server.fastmcp import FastMCP

from .auth import TokenAuth
from .config import Config
from .desktop import Desktop
from .events import event_bus
from .logger import ActionLogger, _gen_session_id
from .manager import DesktopManager, DesktopState
from .access import AccessGuard
from .registry import Registry
from .request_context import get_current_agent, get_current_role, is_admin, set_identity, ADMIN_KEY

log = logging.getLogger("screenbox")

# -- Shared state --

config = Config()


def _on_state_change(event: str, desktop_id: str, data: dict):
    """Forward manager state changes to the event bus."""
    event_bus.emit(event, desktop_id, data)


manager = DesktopManager(config, on_state_change=_on_state_change)
manager.start_idle_checker(interval_seconds=60)

# -- Auth --
# Auth is optional: enabled only when SCREENBOX_MASTER_SECRET is set
# or when ~/.screenbox/master-secret file exists.
# When disabled, all agents have full access (single-user mode).
_auth_enabled = bool(os.environ.get("SCREENBOX_MASTER_SECRET"))
token_auth: TokenAuth | None = TokenAuth(base_dir=config.base_dir) if _auth_enabled else None

# -- Agent Registry (SQLite) --
registry = Registry(config.base_dir / "screenbox.db")

# -- Unified Access Guard --
# Single auth controller for both MCP tools and HTTP API.
# SCREENBOX_REQUIRE_AUTH=true enables strict mode (all operations require auth).
guard = AccessGuard(registry)

AGENT_ID: str = os.environ.get("SCREENBOX_AGENT_ID", os.environ.get("USER", "unknown"))

# For stdio transport: if admin key is set, auto-authenticate as admin
# (stdio = single agent process, identity is fixed for lifetime)
_transport = os.environ.get("SCREENBOX_TRANSPORT", "stdio")
if _transport == "stdio" and ADMIN_KEY:
    set_identity("admin", "admin")
    log.info("stdio transport: auto-authenticated as admin (SCREENBOX_ADMIN_KEY set)")
elif _transport == "stdio" and AGENT_ID != "unknown":
    set_identity(AGENT_ID, "agent")
    log.info("stdio transport: identity set to %s", AGENT_ID)
SESSION_ID: str = _gen_session_id()

# -- Session loggers (one per desktop) --

_loggers: dict[str, ActionLogger] = {}


def _close_loggers() -> None:
    """Close all active session loggers on shutdown."""
    for logger in _loggers.values():
        try:
            logger.close()
        except Exception:
            pass


atexit.register(_close_loggers)


def get_logger(desktop_id: str) -> ActionLogger:
    """Get or create an ActionLogger for a desktop session."""
    if desktop_id not in _loggers:
        _loggers[desktop_id] = ActionLogger(
            config.logs_dir, desktop_id,
            log_keystrokes=config.get("log_keystrokes", False),
            session_id=SESSION_ID,
            agent_id=AGENT_ID,
        )
    return _loggers[desktop_id]


def log_action(desktop_id: str, tool: str, args: dict,
               result: object = None, start_time: float = 0,
               intent: str = "", step: str = "") -> None:
    """Log a tool invocation to the desktop's action log."""
    duration = int((time.time() - start_time) * 1000) if start_time else None
    from .request_context import get_current_ip
    effective_agent = get_current_agent() or AGENT_ID
    source_ip = get_current_ip()
    get_logger(desktop_id).log(
        tool, args, result=result, duration_ms=duration,
        agent_id=effective_agent, intent=intent or None, step=step or None,
        source_ip=source_ip or None,
    )
    # Update last_tool_call to prevent idle auto-pause (#492)
    manager.touch(desktop_id)


def format_recent_actions(desktop_id: str, limit: int = 8) -> str:
    """Format last N actions as a compact [RECENT ACTIONS] block for injection."""
    # Read more than limit to compensate for filtered internal entries
    entries = get_logger(desktop_id).read_recent(limit * 3)
    if not entries:
        return ""
    # Skip internal Desktop-level logs (duplicates of tool-level logs)
    # and intermediate smart_screenshot wrapper
    _SKIP = {"screenshot", "shell", "click", "double_click", "type_text",
             "key", "scroll", "drag", "desktop_smart_screenshot"}
    lines = []
    for e in entries:
        tool = e.get("tool", "?")
        if tool in _SKIP:
            continue
        ts = e.get("timestamp", "")[-13:-1]  # HH:MM:SS.mmm
        args = e.get("args", {})
        compact = " ".join(f"{k}={json.dumps(v)[:40]}" for k, v in args.items()
                           if v and k != "desktop_id")
        dur = f" {e['duration_ms']}ms" if e.get("duration_ms") else ""
        err = " ERROR" if e.get("level") == "error" else ""
        lines.append(f"  {ts} {tool}({compact}){dur}{err}")
    lines = lines[-limit:]
    if not lines:
        return ""
    return "[RECENT ACTIONS]\n" + "\n".join(lines)


# -- Desktop access with validation --

_VALID_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')


def get_desktop(desktop_id: str) -> Desktop:
    """Get Desktop interaction object, validating ID and state.

    Raises ValueError if desktop_id is invalid, not found, paused,
    human-controlled, or locked by another agent.
    """
    if not desktop_id or not _VALID_ID.match(desktop_id):
        raise ValueError(
            f"Invalid desktop_id '{desktop_id}'. "
            "Must be 1-64 chars, alphanumeric/dash/underscore, start with alphanumeric."
        )
    info = manager.get(desktop_id)
    if not info:
        # Try to discover externally-created desktops (dashboard, docker CLI)
        manager._recover_existing()
        info = manager.get(desktop_id)
    if not info:
        raise ValueError(f"Desktop '{desktop_id}' not found. Use desktop_create first.")
    if info.state == DesktopState.HUMAN_CONTROLLED:
        raise ValueError(json.dumps({
            "error": "DESKTOP_HUMAN_CONTROLLED",
            "desktop_id": desktop_id,
            "message": "Human has taken control. Poll desktop_status to check when released.",
        }))
    if info.state == DesktopState.PAUSED:
        raise ValueError(f"Desktop '{desktop_id}' is paused. Use desktop_resume first.")
    if info.state != DesktopState.RUNNING:
        raise ValueError(f"Desktop '{desktop_id}' is not running (state={info.state.value}).")
    # Auto-release expired leases
    if info.acquired_by and info.acquired_at and time.time() - info.acquired_at > manager.lease_ttl:
        info.acquired_by = None
        info.acquired_at = None
        info.session_token = None
    # Resolve identity: per-request contextvar > MCP session > env var
    effective_agent = get_current_agent() or guard.current_agent or AGENT_ID
    effective_role = get_current_role()
    # Admin bypasses all access checks
    if effective_role != "admin":
        if info.acquired_by and info.acquired_by != effective_agent:
            raise ValueError(
                f"Desktop '{desktop_id}' is locked by agent '{info.acquired_by}'. "
                f"Use desktop_release or wait for lock expiry."
            )
        # Check ownership/assignment
        guard.check_desktop_access(effective_agent, desktop_id)
    # Implicit heartbeat: every tool call refreshes the lease
    if info.acquired_by and info.acquired_by == effective_agent:
        info.acquired_at = time.time()
    return Desktop(manager, desktop_id,
                   action_logger=get_logger(desktop_id), agent_id=effective_agent)


# -- MCP server instance --

MCP_INSTRUCTIONS = """\
Screenbox: virtual desktops for AI agents.

**Start here:** call screenbox_info() to see architecture, config, and running desktops.

## Authentication

Identity is resolved from the `X-API-Key` or `Authorization: Bearer` header.
- **Admin** (API token or admin key): full access to all desktops
- **Agent** (registered api_key): access to own + shared desktops only
- **New agent?** Register first: `desktop_manage(action="register", agent_id="my-name")` \
-> save the returned api_key, pass it via X-API-Key header

## Tools

**Core (8):** desktop_screenshot, desktop_look, desktop_click, desktop_batch, \
desktop_help, desktop_type, desktop_key, desktop_shell
**Dispatchers (4):** desktop_chrome(action=...), desktop_window(action=...), \
desktop_file(action=...), desktop_manage(action=...)
**System (2):** screenbox_info (architecture, config, desktops), \
screenbox_logs(desktop_id, limit?) -- read action history

## MANDATORY Workflow: screenshot -> look -> click

**NEVER click without looking first.** desktop_look is the 2nd most used tool \
after screenshot.

1. `desktop_screenshot(id)` -- see what's on screen
2. `desktop_look(id, cell=N)` -- OCR the area of interest, get precise coordinates
3. `desktop_click(id, x, y)` -- click using coordinates from look

## Interaction Priority (use highest available)

1. **Chrome semantics (for browser content):** desktop_chrome(action="page_read") \
for reading text, page_map for interactive elements. One call replaces 3-4 OCR cells. \
ALWAYS try Chrome semantics FIRST when working with browser pages.
2. **Window API:** desktop_window(action="minimize/maximize/activate/close") \
-- instant, no coordinates
3. **Keyboard:** desktop_key("ctrl+s"), desktop_key("Tab"), desktop_key("Alt+F4") \
-- faster than clicking
4. **Look + Click:** desktop_screenshot -> desktop_look(cell) -> desktop_click(x, y) \
-- precise OCR coords. Use desktop_look ONLY for click coordinates or non-browser windows.

## Chrome Semantics (PREFERRED for browser)

When Chrome is open, ALWAYS prefer these over screenshot+OCR:
- `desktop_chrome(action="page_read")` -- full page text, one call
- `desktop_chrome(action="page_map")` -- interactive elements with coordinates
- `desktop_chrome(action="view_read")` -- visible viewport text only
- `desktop_chrome(action="navigate", url=...)` -- go to URL directly

Use desktop_look ONLY when you need precise pixel coordinates for clicking, \
or when working with non-browser windows (file manager, terminal, etc.).

## Observe Mode (desktop_click)

desktop_click returns image + OCR around click point by default (observe=true).
This eliminates the need for a separate screenshot after clicking.

- After click: check returned image and OCR elements to verify result
- Full screenshot only needed for initial orientation or major screen changes
- observe=false: use when you don't need visual feedback (rapid sequential clicks)

## Text Input Strategy (STRICT)

**System apps (LibreOffice, terminal, file dialogs, text editors) -- use CLIPBOARD:**
```
desktop_shell(id, "printf 'col1\\tcol2\\nval1\\tval2' | DISPLAY=:99 xclip -selection clipboard")
desktop_key(id, "ctrl+v")
```
- Instant paste, no per-character delay, no timeout on long text
- Tab (\\t) and newline (\\n) work correctly in spreadsheet cells
- Use desktop_type ONLY for short inputs (<20 chars)

**Browser (Chrome, Firefox) -- use KEYBOARD (desktop_type):**
- desktop_type for form inputs and search fields
- Use clipboard (ctrl+v) only for long text in non-browser apps

| Context | Method | Why |
|---------|--------|-----|
| Spreadsheet cells | clipboard | Speed, no timeout |
| Terminal commands | clipboard | Speed, accuracy |
| File dialog names | desktop_type | Short text |
| Browser forms | desktop_type | Compatibility |
| Browser search | desktop_type | Compatibility |

## Key Patterns

- Standard click: desktop_screenshot -> desktop_look(cell=N) -> desktop_click(x, y) \
[observe returns image]
- Quick verify after action: desktop_look(cell=0) -- OCR around current cursor, \
no full screenshot
- Menu navigation: desktop_click(menu) -> desktop_look(cell=N) -> desktop_click(item)
- Tab rename (LibreOffice): desktop_click(tab, clicks=2) + desktop_type(name) + \
key(Return) -- NOT context menu
- Dark themes: desktop_screenshot(enhance=true) for better visibility
- Popup menus: if desktop_click times out, use \
desktop_shell("DISPLAY=:99 xdotool mousemove X Y && sleep 0.1 && \
DISPLAY=:99 xdotool click 1")

## GUI App Readiness (MANDATORY before typing/pasting)

After launching any GUI application (mousepad, libreoffice, gimp, etc.):
1. **Take a screenshot** to confirm the app window loaded
2. **Check for dialogs** -- restore session, update, crash recovery, license. \
Dismiss them BEFORE typing or pasting
3. **Verify the target window has focus** -- use desktop_window(action="activate") \
if unsure
4. **Only then** send keystrokes (desktop_key, desktop_type)

If you skip this, keystrokes go to the wrong window or into nothing. \
The desktop does NOT guarantee focus order after app launch.

**Saving files:** For structured text, prefer `desktop_shell("cat > file << 'EOF' ...")` \
over GUI paste. It is faster, more reliable, and avoids focus issues. \
Open the file in an editor AFTER writing it, for the user to see.

## Intent (MANDATORY)

Every tool call MUST include the `intent` parameter explaining WHY you are performing this action.
Good intent describes the goal, not the action itself.
- GOOD: intent="Find the Save button in the dialog"
- GOOD: intent="Navigate to pricing page to compare plans"
- BAD: intent="Click button" (too vague)
- BAD: intent="" (empty)

Intent is logged and used for knowledge compilation -- it helps future agents understand
the reasoning behind each action.

## Anti-Patterns

- NEVER guess coordinates from full screenshots -- use look to get OCR coords
- NEVER click title bar buttons -- use desktop_window actions
- NEVER hover twice in menus -- second hover kills submenu
- NEVER loop >2 times on wrong coordinates -- zoom in closer
- NEVER use desktop_type for long text in system apps -- use clipboard
- NEVER use clipboard paste in browser forms -- use desktop_type
- NEVER use right-click context menu for tab rename -- use double-click
"""

# Determine host for DNS rebinding protection.
# When deployed on a network (0.0.0.0), disable DNS rebinding checks
# so external clients can connect using the server's real hostname/IP.
_mcp_host = os.environ.get("SCREENBOX_HOST", "127.0.0.1")
_mcp_kwargs: dict = {"instructions": MCP_INSTRUCTIONS, "stateless_http": True}
if _mcp_host != "127.0.0.1":
    _mcp_kwargs["host"] = _mcp_host

mcp = FastMCP("screenbox", **_mcp_kwargs)


# -- Register all tools from tools/ package --

from .tools import register_all  # noqa: E402
register_all(mcp, get_desktop, lambda: manager, log_action)

# -- Register HTTP API for dashboard integration --
from .http_api import register as register_http_api  # noqa: E402
register_http_api(mcp)
