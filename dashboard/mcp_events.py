"""Screenbox Dashboard -- MCP event stream consumer.

Connects to MCP SSE /api/events for real-time state changes.
Periodically fetches screenshots from MCP API.
Replaces the docker ps polling in _background_collector.
"""

import asyncio
import logging
import time

import aiohttp

from config import MCP_API_URL, SCREENSHOT_TTL
from state import (
    _cache, _cache_lock, _screenshot_cache, _screenshot_lock,
    emit_state_change, emit_full_state,
)
import mcp_client

log = logging.getLogger("dashboard.mcp_events")


def _log(msg, *args):
    """Print to stdout (dashboard convention) and log."""
    text = msg % args if args else msg
    print(f"[mcp-events] {text}", flush=True)
    log.info(msg, *args)

# Track known desktop states for diff detection
_known_states: dict[str, str] = {}


async def _fetch_desktop_list():
    """Fetch full desktop list from MCP API and update cache."""
    body, status = await mcp_client.mcp_get("/api/desktop/list")
    if status != 200 or not isinstance(body, list):
        return []

    desktops = []
    for d in body:
        desktops.append({
            "id": d.get("id", ""),
            "name": d.get("name", f"screenbox-{d.get('id', '')}"),
            "state": d.get("state", "unknown"),
            "label": d.get("label"),
            "vnc_port": d.get("vnc_port"),
            "novnc_port": d.get("novnc_port") or d.get("rdp_port"),
            "rdp_port": d.get("rdp_port"),
            "resolution": d.get("resolution", ""),
            "image": d.get("image"),
            "acquired_by": d.get("acquired_by"),
            "acquired_at": d.get("acquired_at"),
            "assigned_to": d.get("assigned_to"),
            "assigned_name": d.get("assigned_name"),
            "resources": {},
        })

    with _cache_lock:
        # Merge: keep existing resource data, update state/ports
        old_by_id = {d["id"]: d for d in _cache["desktops"]}
        for d in desktops:
            old = old_by_id.get(d["id"])
            if old and old.get("resources"):
                d["resources"] = old["resources"]
        _cache["desktops"] = desktops
        _cache["ts"] = time.time()

    return desktops


async def _fetch_screenshots(desktops: list):
    """Fetch screenshots for running desktops from MCP API."""
    for d in desktops:
        did = d.get("id", "")
        if d.get("state") != "running":
            with _screenshot_lock:
                _screenshot_cache.pop(did, None)
            continue
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            ) as session:
                headers = mcp_client._headers()
                # Don't send Content-Type for GET
                headers.pop("Content-Type", None)
                async with session.get(
                    f"{MCP_API_URL}/api/desktop/screenshot",
                    params={"id": did}, headers=headers,
                ) as resp:
                    if resp.status == 200:
                        data = await resp.read()
                        if data:
                            with _screenshot_lock:
                                _screenshot_cache[did] = data
                    else:
                        with _screenshot_lock:
                            _screenshot_cache.pop(did, None)
        except Exception as e:
            _log("Screenshot fetch failed for %s: %s", did, e)


async def _process_event(event: dict):
    """Process a single SSE event from MCP."""
    global _known_states

    ev_type = event.get("event", "")
    desktop_id = event.get("desktop_id", "")

    if ev_type == "init":
        # Full state reset from MCP
        desktops = event.get("desktops", [])
        _known_states.clear()
        for d in desktops:
            did = d.get("id", "")
            state = d.get("state", "unknown")
            _known_states[did] = state
        # Fetch full list to populate cache
        await _fetch_desktop_list()
        _log("Init: %d desktops from MCP", len(desktops))
        return

    if not desktop_id:
        return

    # State change events
    state = event.get("state", "")
    if ev_type == "destroyed":
        state = "destroyed"
    elif ev_type in ("created", "started", "resumed"):
        state = state or "running"
    elif ev_type in ("paused",):
        state = state or "paused"
    elif ev_type in ("stopped",):
        state = state or "stopped"
    elif ev_type in ("starting",):
        state = state or "starting"
    elif ev_type in ("error",):
        state = state or "error"
    elif ev_type in ("recording_started", "recording_stopped"):
        # Recording events don't change desktop state
        return

    if state:
        old = _known_states.get(desktop_id)
        _known_states[desktop_id] = state
        if state == "destroyed":
            _known_states.pop(desktop_id, None)
        # Emit to dashboard WebSocket subscribers
        if state != old:
            emit_state_change(desktop_id, state)
        # Refresh desktop list from MCP
        await _fetch_desktop_list()


async def mcp_events_consumer():
    """Main loop: connect to MCP SSE and process events.

    Also periodically fetches screenshots.
    Reconnects on disconnect with backoff.
    """
    backoff = 1
    headers = mcp_client._headers()
    # Remove Content-Type for GET requests
    headers.pop("Content-Type", None)

    while True:
        try:
            _log("Connecting to MCP SSE at %s/api/events", MCP_API_URL)
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=0)  # no timeout for SSE
            ) as session:
                async with session.get(
                    f"{MCP_API_URL}/api/events", headers=headers
                ) as resp:
                    if resp.status != 200:
                        _log("MCP SSE returned %d", resp.status)
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30)
                        continue

                    backoff = 1
                    _log("Connected to MCP SSE")

                    buffer = ""
                    async for chunk in resp.content.iter_any():
                        text = chunk.decode("utf-8", errors="replace")
                        buffer += text
                        # Process complete SSE messages
                        while "\n\n" in buffer:
                            msg, buffer = buffer.split("\n\n", 1)
                            for line in msg.split("\n"):
                                if line.startswith("data: "):
                                    try:
                                        import json
                                        event = json.loads(line[6:])
                                        await _process_event(event)
                                    except Exception as e:
                                        log.warning("Bad SSE data: %s", e)
                                # Ignore comments (: keepalive)

        except asyncio.CancelledError:
            _log("MCP events consumer cancelled")
            return
        except Exception as e:
            _log("MCP SSE error: %s, reconnecting in %ds", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)


async def _screenshot_loop():
    """Periodically fetch screenshots from MCP API."""
    _log("Screenshot loop started (interval=%ds)", SCREENSHOT_TTL)
    # Wait for MCP events consumer to populate cache
    await asyncio.sleep(3)
    while True:
        try:
            with _cache_lock:
                desktops = list(_cache["desktops"])
            if desktops:
                await _fetch_screenshots(desktops)
        except Exception as e:
            _log("Screenshot fetch error: %s", e)
        await asyncio.sleep(SCREENSHOT_TTL)


async def resource_collector():
    """Periodically refresh desktop list + resource stats from MCP API."""
    _log("Resource collector started (interval=15s)")
    while True:
        try:
            # Refresh desktop list (picks up assigned_name, state changes)
            await _fetch_desktop_list()

            body, status = await mcp_client.mcp_get("/api/desktop/stats")
            if status == 200 and isinstance(body, dict):
                updated = []
                with _cache_lock:
                    for d in _cache["desktops"]:
                        res = body.get(d["id"])
                        if res:
                            d["resources"] = res
                    updated = list(_cache["desktops"])
                if updated:
                    emit_full_state(updated)
                _log("Resources updated: %d desktops", len(body))
        except Exception as e:
            _log("Resource collection error: %s", e)
        await asyncio.sleep(15)
