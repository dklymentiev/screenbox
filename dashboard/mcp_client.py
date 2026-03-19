"""Screenbox Dashboard -- MCP API client.

Proxies desktop lifecycle operations to the MCP HTTP API
instead of calling docker directly.
"""

import aiohttp
import logging
import os

log = logging.getLogger("dashboard.mcp_client")

MCP_API_URL = os.environ.get("SCREENBOX_MCP_API_URL", "http://screenbox-mcp:8080")
MCP_API_TOKEN = os.environ.get("SCREENBOX_API_TOKEN", "")

# Longer timeout for create (includes snapshot restore) and pause (includes snapshot)
_TIMEOUT = aiohttp.ClientTimeout(total=120)


def _headers():
    h = {"Content-Type": "application/json"}
    if MCP_API_TOKEN:
        h["Authorization"] = f"Bearer {MCP_API_TOKEN}"
    return h


async def mcp_get(path: str, params: dict = None) -> tuple[dict | list, int]:
    """GET request to MCP API. Returns (json_body, status_code)."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.get(
                f"{MCP_API_URL}{path}", params=params, headers=_headers()
            ) as resp:
                body = await resp.json()
                return body, resp.status
    except Exception as e:
        log.error("MCP GET %s failed: %s", path, e)
        return {"error": f"MCP unavailable: {e}"}, 502


async def mcp_post(path: str, data: dict) -> tuple[dict, int]:
    """POST request to MCP API. Returns (json_body, status_code)."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.post(
                f"{MCP_API_URL}{path}", json=data, headers=_headers()
            ) as resp:
                body = await resp.json()
                return body, resp.status
    except Exception as e:
        log.error("MCP POST %s failed: %s", path, e)
        return {"error": f"MCP unavailable: {e}"}, 502


async def mcp_delete(path: str, data: dict) -> tuple[dict, int]:
    """DELETE request to MCP API. Returns (json_body, status_code)."""
    try:
        async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
            async with session.delete(
                f"{MCP_API_URL}{path}", json=data, headers=_headers()
            ) as resp:
                body = await resp.json()
                return body, resp.status
    except Exception as e:
        log.error("MCP DELETE %s failed: %s", path, e)
        return {"error": f"MCP unavailable: {e}"}, 502


# -- Convenience wrappers --

async def create_desktop(desktop_id: str, resolution: str = "",
                         image: str = "", label: str = "") -> dict:
    """Create desktop via MCP API."""
    data = {"id": desktop_id}
    if resolution:
        data["resolution"] = resolution
    if image:
        data["image"] = image
    if label:
        data["label"] = label
    body, status = await mcp_post("/api/desktop/create", data)
    return body


async def destroy_desktop(desktop_id: str, save_snapshot: bool = True) -> dict:
    """Destroy desktop via MCP API."""
    body, status = await mcp_post("/api/desktop/destroy", {
        "id": desktop_id,
        "auto_snapshot": save_snapshot,
    })
    return body


async def control_desktop(desktop_id: str, action: str) -> dict:
    """Control desktop (pause/resume/stop/start) via MCP API."""
    body, status = await mcp_post("/api/desktop/control", {
        "id": desktop_id,
        "action": action,
    })
    return body


async def record_control(desktop_id: str, action: str) -> dict:
    """Start/stop recording via MCP API."""
    body, status = await mcp_post("/api/desktop/record", {
        "id": desktop_id,
        "action": action,
    })
    return body


async def record_status(desktop_id: str) -> dict:
    """Get recording status via MCP API."""
    body, status = await mcp_get("/api/desktop/record", {"id": desktop_id})
    return body


async def get_container_ip(desktop_id: str) -> str | None:
    """Get container IP via MCP API."""
    body, status = await mcp_get("/api/desktop/ip", {"id": desktop_id})
    if status == 200:
        return body.get("ip")
    return None


def get_container_ip_sync(desktop_id: str) -> str | None:
    """Sync version of get_container_ip for VNC proxy."""
    import urllib.request
    import json as _json
    url = f"{MCP_API_URL}/api/desktop/ip?id={desktop_id}"
    headers = {}
    if MCP_API_TOKEN:
        headers["Authorization"] = f"Bearer {MCP_API_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            return data.get("ip")
    except Exception:
        return None


async def send_input(desktop_id: str, body: dict) -> dict:
    """Send input to desktop via MCP API."""
    data = dict(body)
    data["id"] = desktop_id
    result, status = await mcp_post("/api/desktop/input", data)
    return result


async def health() -> dict:
    """Check MCP API health."""
    body, status = await mcp_get("/api/health")
    return body


def validate_share_token(token: str) -> dict | None:
    """Sync: validate share token via MCP API. Returns {desktop_id, ...} or None."""
    import urllib.request
    import urllib.parse
    import json as _json
    url = f"{MCP_API_URL}/api/desktop/share/validate?{urllib.parse.urlencode({'token': token})}"
    headers = {}
    if MCP_API_TOKEN:
        headers["Authorization"] = f"Bearer {MCP_API_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = _json.loads(resp.read())
            if "desktop_id" in data:
                return data
    except Exception:
        pass
    return None
