"""Screenbox Dashboard -- mutable shared state, cache, and event bus."""

import asyncio
import json
import os
import threading
import time

from config import CONFIG_FILE

# Mutable settings (loaded from config file)
_settings = {
    "port_start": 16080,
    "port_end": 16400,
    "max_desktops": 20,
    "memory_limit": "2048m",
    "shm_size": "256m",
    "chrome_url": "none",
}


def _load_settings():
    try:
        with open(CONFIG_FILE) as f:
            _settings.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass


def _save_settings():
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(_settings, f, indent=2)


_load_settings()

# Cache for desktop data and screenshots
_cache = {"desktops": [], "ts": 0}
_cache_lock = threading.Lock()
_screenshot_cache: dict[str, bytes] = {}
_screenshot_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Server-side state overrides (transition states)
# ---------------------------------------------------------------------------
# When dashboard API triggers an action (create, destroy, pause, etc.),
# we set a transition state that overrides docker state until docker catches up.
# Format: {desktop_id: {"state": "pausing", "ts": timestamp}}
_state_overrides: dict[str, dict] = {}
_override_lock = threading.Lock()
_OVERRIDE_TTL = 30  # seconds -- fallback to docker state if override is stale


def set_override(desktop_id: str, state: str, expect: str = None):
    """Set a transition state override for a desktop.

    Args:
        state: transition state to show (e.g. "starting", "pausing")
        expect: expected final docker state (e.g. "running", "paused").
                Override auto-clears when docker reaches this state.
                If None, override must be cleared manually.
    """
    with _override_lock:
        _state_overrides[desktop_id] = {
            "state": state,
            "expect": expect,
            "ts": time.time(),
        }
    emit_state_change(desktop_id, state)


def clear_override(desktop_id: str):
    """Remove state override (docker state takes over)."""
    with _override_lock:
        _state_overrides.pop(desktop_id, None)


def get_override(desktop_id: str, docker_state: str = None) -> str | None:
    """Get active override state, or None if expired/docker caught up.

    If docker_state matches the expected final state, auto-clear the override.
    """
    with _override_lock:
        ov = _state_overrides.get(desktop_id)
        if not ov:
            return None
        # TTL expired
        if time.time() - ov["ts"] > _OVERRIDE_TTL:
            del _state_overrides[desktop_id]
            return None
        # Docker caught up to expected state -- auto-clear
        if docker_state and ov.get("expect") and docker_state == ov["expect"]:
            del _state_overrides[desktop_id]
            return None
        return ov["state"]


# ---------------------------------------------------------------------------
# Event bus: WebSocket subscribers for real-time state changes
# ---------------------------------------------------------------------------

_ws_subscribers: set = set()  # set of asyncio.Queue
_ws_lock = threading.Lock()


def subscribe() -> asyncio.Queue:
    """Add a new WebSocket subscriber. Returns a Queue to await events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=64)
    with _ws_lock:
        _ws_subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue):
    """Remove a WebSocket subscriber."""
    with _ws_lock:
        _ws_subscribers.discard(q)


def emit_state_change(desktop_id: str, state: str, extra: dict = None):
    """Broadcast state change to all WebSocket subscribers.

    This is the ONLY event type. Simple: desktop X is now in state Y.
    """
    msg = {"desktop_id": desktop_id, "state": state, "ts": time.time()}
    if extra:
        msg.update(extra)
    with _ws_lock:
        dead = []
        for q in _ws_subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _ws_subscribers.discard(q)


def emit_full_state(desktops: list):
    """Broadcast full desktop list to all subscribers (used by collector on diff)."""
    msg = {"full_state": True, "desktops": desktops, "ts": time.time()}
    with _ws_lock:
        dead = []
        for q in _ws_subscribers:
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            _ws_subscribers.discard(q)


# Keep old emit_event as alias for backward compat with collector
def emit_event(event: str, desktop_id: str, data: dict = None):
    """Legacy alias -- maps old events to state changes."""
    state = None
    if data and "state" in data:
        state = data["state"]
    elif "destroyed" in event:
        state = "destroyed"
    elif "created" in event:
        state = "running"
    if state:
        emit_state_change(desktop_id, state)



def get_desktops():
    """Return cached desktop data with overrides.

    State comes from MCP events (no docker ps polling).
    running + no screenshot = starting (visual channel not ready yet).
    """
    with _cache_lock:
        desktops = [dict(d) for d in _cache["desktops"]]
    # Apply transition overrides (server-side, not client-side)
    for d in desktops:
        ov = get_override(d["id"], docker_state=d["state"])
        if ov:
            d["state"] = ov
    # running without screenshot = starting
    with _screenshot_lock:
        for d in desktops:
            if d["state"] == "running" and d["id"] not in _screenshot_cache:
                d["state"] = "starting"
    return desktops
