"""Screenbox Dashboard -- logs API route handlers.

Thin proxy to MCP HTTP API. All log reading logic lives in MCP.
"""

from aiohttp import web

from config import _VALID_ID
import mcp_client


async def handle_logs_recent(request: web.Request) -> web.Response:
    """GET /api/logs/{desktop_id}/recent?limit=100&after=<timestamp>"""
    desktop_id = request.match_info["desktop_id"]
    if not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop ID"}, status=400)

    params = {}
    if request.query.get("limit"):
        params["limit"] = request.query["limit"]
    if request.query.get("after"):
        params["after"] = request.query["after"]

    body, status = await mcp_client.mcp_get(f"/api/logs/{desktop_id}/recent", params)
    return web.json_response(body, status=status)


async def handle_logs_download(request: web.Request) -> web.Response:
    """GET /api/logs/{desktop_id}/download -- proxy JSONL download from MCP."""
    desktop_id = request.match_info["desktop_id"]
    if not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop ID"}, status=400)

    import aiohttp
    url = f"{mcp_client.MCP_API_URL}/api/logs/{desktop_id}/download"
    headers = {}
    if mcp_client.MCP_API_TOKEN:
        headers["Authorization"] = f"Bearer {mcp_client.MCP_API_TOKEN}"

    try:
        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    body = await resp.json()
                    return web.json_response(body, status=resp.status)
                content = await resp.read()
                return web.Response(
                    body=content,
                    content_type="application/x-ndjson",
                    headers={
                        "Content-Disposition": f'attachment; filename="{desktop_id}-logs.jsonl"',
                        "Cache-Control": "no-cache",
                    },
                )
    except Exception as e:
        return web.json_response({"error": f"MCP unavailable: {e}"}, status=502)


def setup(app: web.Application):
    """Register logs API routes."""
    app.router.add_get("/api/logs/{desktop_id}/recent", handle_logs_recent)
    app.router.add_get("/api/logs/{desktop_id}/download", handle_logs_download)
