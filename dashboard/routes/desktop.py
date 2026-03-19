"""Screenbox Dashboard -- desktop API route handlers.

All operations proxy to MCP HTTP API.
System stats and storage use local filesystem only (no docker).
"""

import asyncio

from aiohttp import web

from config import _VALID_ID
from state import (
    _settings, _save_settings, _screenshot_cache, _screenshot_lock,
    get_desktops, set_override, emit_state_change,
)
from docker_helpers import (
    _get_system_stats, _list_storage, _delete_storage,
)
import mcp_client


async def handle_api_desktops(request: web.Request) -> web.Response:
    data = get_desktops()
    return web.json_response(data)


async def handle_api_screenshot(request: web.Request) -> web.Response:
    desktop_id = request.query.get("id", "")
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.Response(status=400, text="Bad desktop_id")
    with _screenshot_lock:
        data = _screenshot_cache.get(desktop_id, b"")
    if data:
        return web.Response(
            body=data,
            content_type="image/jpeg",
            headers={"Cache-Control": "no-cache"},
        )
    return web.Response(status=503, text="Screenshot unavailable")


async def handle_api_system(request: web.Request) -> web.Response:
    from config import __version__
    data = await asyncio.to_thread(_get_system_stats)
    data["settings"] = _settings
    data["desktop_count"] = len(get_desktops())
    data["version"] = __version__
    return web.json_response(data)


async def handle_api_storage(request: web.Request) -> web.Response:
    data = await asyncio.to_thread(_list_storage)
    return web.json_response(data)


async def handle_api_create(request: web.Request) -> web.Response:
    body = await request.json()
    desktop_id = body.get("id", "").strip()
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop ID"}, status=400)
    # Transition: starting -> auto-clears when docker says "running"
    set_override(desktop_id, "starting", expect="running")
    result = await mcp_client.create_desktop(
        desktop_id,
        resolution=body.get("resolution", ""),
        image=body.get("image", ""),
    )
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)


async def handle_api_destroy(request: web.Request) -> web.Response:
    body = await request.json()
    desktop_id = body.get("id", "").strip()
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop ID"}, status=400)
    # Transition: destroying -- no expect, clear manually after destroy
    set_override(desktop_id, "destroying")
    result = await mcp_client.destroy_desktop(desktop_id)
    if result.get("ok"):
        from state import clear_override
        clear_override(desktop_id)
        emit_state_change(desktop_id, "destroyed")
    return web.json_response(result)


async def handle_api_control(request: web.Request) -> web.Response:
    body = await request.json()
    desktop_id = body.get("id", "").strip()
    action = body.get("action", "").strip()
    if not desktop_id or not action:
        return web.json_response({"error": "Missing id or action"}, status=400)
    if not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop_id"}, status=400)
    # Transition with expected final state -- auto-clears when docker catches up
    transition_map = {
        "pause":   ("pausing",  "paused"),
        "unpause": ("resuming", "running"),
        "stop":    ("stopping", "stopped"),
        "start":   ("starting", "running"),
    }
    tr = transition_map.get(action)
    if tr:
        set_override(desktop_id, tr[0], expect=tr[1])
    result = await mcp_client.control_desktop(desktop_id, action)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)


async def handle_api_storage_delete(request: web.Request) -> web.Response:
    body = await request.json()
    desktop_id = body.get("id", "").strip()
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid ID"}, status=400)
    result = await asyncio.to_thread(_delete_storage, desktop_id)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)


async def handle_api_input(request: web.Request) -> web.Response:
    body = await request.json()
    desktop_id = body.get("id", "").strip()
    action = body.get("action", "").strip()
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop ID"}, status=400)
    if action not in ("click", "mousemove", "type", "key", "mousedown", "mouseup", "scroll"):
        return web.json_response({"error": "Invalid action"}, status=400)
    result = await mcp_client.send_input(desktop_id, body)
    status = 200 if result.get("ok") else 400
    return web.json_response(result, status=status)


async def handle_api_share(request: web.Request) -> web.Response:
    """Proxy share creation to MCP API."""
    body = await request.json()
    desktop_id = body.get("desktop_id", "").strip()
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop ID"}, status=400)
    result, status = await mcp_client.mcp_post("/api/desktop/share", body)
    return web.json_response(result, status=status)


async def handle_api_shares(request: web.Request) -> web.Response:
    """Proxy share list to MCP API."""
    desktop_id = request.query.get("desktop_id", "")
    params = {"desktop_id": desktop_id} if desktop_id else {}
    result, status = await mcp_client.mcp_get("/api/desktop/shares", params)
    return web.json_response(result, status=status)


async def handle_api_share_revoke(request: web.Request) -> web.Response:
    """Proxy share revocation to MCP API (DELETE)."""
    body = await request.json()
    result, status = await mcp_client.mcp_delete("/api/desktop/share", body)
    return web.json_response(result, status=status)


async def handle_api_share_validate(request: web.Request) -> web.Response:
    """Public: proxy share validation to MCP API."""
    token = request.query.get("token", "")
    result, status = await mcp_client.mcp_get(
        "/api/desktop/share/validate", {"token": token})
    return web.json_response(result, status=status)


async def handle_api_settings(request: web.Request) -> web.Response:
    body = await request.json()
    changed = False
    for key in ("port_start", "port_end", "max_desktops"):
        if key in body:
            _settings[key] = int(body[key])
            changed = True
    for key in ("memory_limit", "shm_size", "chrome_url"):
        if key in body:
            _settings[key] = str(body[key])
            changed = True
    if changed:
        _save_settings()
    return web.json_response({"ok": True, "settings": _settings})


def setup(app: web.Application):
    """Register all desktop API routes."""
    app.router.add_get("/api/desktops", handle_api_desktops)
    app.router.add_get("/api/screenshot", handle_api_screenshot)
    app.router.add_get("/api/system", handle_api_system)
    app.router.add_get("/api/storage", handle_api_storage)
    app.router.add_post("/api/create", handle_api_create)
    app.router.add_post("/api/destroy", handle_api_destroy)
    app.router.add_post("/api/control", handle_api_control)
    app.router.add_post("/api/storage/delete", handle_api_storage_delete)
    app.router.add_post("/api/input", handle_api_input)
    app.router.add_post("/api/settings", handle_api_settings)
    # Share links
    app.router.add_post("/api/share", handle_api_share)
    app.router.add_get("/api/shares", handle_api_shares)
    app.router.add_post("/api/share/revoke", handle_api_share_revoke)
    app.router.add_get("/api/desktop/share/validate", handle_api_share_validate)
