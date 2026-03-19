"""Screenbox Dashboard -- knowledge API route handlers.

Thin proxy to MCP HTTP API. All knowledge logic lives in MCP.
Dashboard only forwards requests and handles file uploads (multipart).
All endpoints pass ?desktop_id= for per-desktop knowledge scoping.
"""

import json
import os
import re

from aiohttp import web

import mcp_client


def _desktop_param(request: web.Request) -> dict:
    """Extract desktop_id from query and return as params dict for MCP."""
    did = request.query.get("desktop_id", "")
    return {"desktop_id": did} if did else {}


async def handle_knowledge_list(request: web.Request) -> web.Response:
    """GET /api/knowledge -- list all knowledge entries."""
    body, status = await mcp_client.mcp_get("/api/knowledge", _desktop_param(request))
    return web.json_response(body, status=status)


async def handle_knowledge_desktops(request: web.Request) -> web.Response:
    """GET /api/knowledge/desktops -- list desktops with own knowledge."""
    body, status = await mcp_client.mcp_get("/api/knowledge/desktops")
    return web.json_response(body, status=status)


async def handle_knowledge_get(request: web.Request) -> web.Response:
    """GET /api/knowledge/{slug} -- get full knowledge for an app."""
    slug = request.match_info["slug"]
    params = {}
    if request.query.get("kind"):
        params["kind"] = request.query["kind"]
    params.update(_desktop_param(request))
    body, status = await mcp_client.mcp_get(f"/api/knowledge/{slug}", params)
    return web.json_response(body, status=status)


async def handle_knowledge_add(request: web.Request) -> web.Response:
    """POST /api/knowledge/{slug}/facts -- add a fact."""
    slug = request.match_info["slug"]
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    # Pass desktop_id from query into body for MCP
    did = request.query.get("desktop_id", "")
    if did:
        data["desktop_id"] = did
    body, status = await mcp_client.mcp_post(f"/api/knowledge/{slug}/facts", data)
    return web.json_response(body, status=status)


async def handle_knowledge_delete_app(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/{slug} -- delete entire app knowledge."""
    slug = request.match_info["slug"]
    params = {}
    if request.query.get("kind"):
        params["kind"] = request.query["kind"]
    params.update(_desktop_param(request))
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    body, status = await mcp_client.mcp_delete(f"/api/knowledge/{slug}?{qs}", {})
    return web.json_response(body, status=status)


async def handle_knowledge_delete_fact(request: web.Request) -> web.Response:
    """DELETE /api/knowledge/{slug}/facts/{index} -- remove a fact."""
    slug = request.match_info["slug"]
    index = request.match_info["index"]
    params = {}
    if request.query.get("kind"):
        params["kind"] = request.query["kind"]
    params.update(_desktop_param(request))
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    body, status = await mcp_client.mcp_delete(
        f"/api/knowledge/{slug}/facts/{index}?{qs}", {}
    )
    return web.json_response(body, status=status)


async def handle_knowledge_importable(request: web.Request) -> web.Response:
    """GET /api/knowledge/importable -- list available knowledge files."""
    body, status = await mcp_client.mcp_get("/api/knowledge/importable", _desktop_param(request))
    return web.json_response(body, status=status)


async def handle_knowledge_import_server(request: web.Request) -> web.Response:
    """POST /api/knowledge/import-server -- import a knowledge file."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    body, status = await mcp_client.mcp_post("/api/knowledge/import-server", data)
    return web.json_response(body, status=status)


_MAX_UPLOAD_SIZE = 1 * 1024 * 1024  # 1MB
_SAFE_FILENAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,100}\.json$')


async def handle_knowledge_upload(request: web.Request) -> web.Response:
    """POST /api/knowledge/upload -- upload .json knowledge file.

    Accepts multipart form with file + optional mode/desktop_id fields.
    Parses locally (multipart), then forwards JSON payload to MCP.
    """
    if request.content_length and request.content_length > _MAX_UPLOAD_SIZE:
        return web.json_response({"error": "File too large (max 1MB)"}, status=413)

    try:
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            return web.json_response({"error": "No file field in upload"}, status=400)

        # Read with size limit
        chunks = []
        total = 0
        while True:
            chunk = await field.read_chunk(8192)
            if not chunk:
                break
            total += len(chunk)
            if total > _MAX_UPLOAD_SIZE:
                return web.json_response({"error": "File too large (max 1MB)"}, status=413)
            chunks.append(chunk)

        raw = b"".join(chunks)
        if not raw:
            return web.json_response({"error": "Empty file"}, status=400)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return web.json_response({"error": f"Invalid JSON: {e}"}, status=400)

        facts = data.get("facts")
        if not isinstance(facts, list):
            return web.json_response({"error": "Missing or invalid 'facts' array"}, status=400)

        raw_name = os.path.basename(field.filename or "upload.json")
        slug = data.get("slug", raw_name.replace(".json", "").lower().replace(" ", "-"))
        slug = re.sub(r'[^a-z0-9-]', '-', slug.lower()).strip('-')
        if not slug:
            return web.json_response({"error": "Invalid slug"}, status=400)

        # Read optional extra fields (mode, desktop_id)
        mode = "merge"
        desktop_id = ""
        while True:
            try:
                extra = await reader.next()
                if extra is None:
                    break
                val = (await extra.read()).decode().strip()
                if extra.name == "mode" and val in ("merge", "replace"):
                    mode = val
                elif extra.name == "desktop_id":
                    desktop_id = val
            except Exception:
                break

        payload = {
            "slug": slug,
            "app": data.get("app", data.get("name", slug)),
            "kind": data.get("kind", "app"),
            "mode": mode,
            "facts": facts,
        }
        if desktop_id:
            payload["desktop_id"] = desktop_id
        body, status = await mcp_client.mcp_post("/api/knowledge/upload", payload)
        return web.json_response(body, status=status)

    except web.HTTPException:
        raise
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


def setup(app: web.Application):
    """Register knowledge API routes."""
    app.router.add_get("/api/knowledge", handle_knowledge_list)
    app.router.add_get("/api/knowledge/desktops", handle_knowledge_desktops)
    app.router.add_get("/api/knowledge/importable", handle_knowledge_importable)
    app.router.add_post("/api/knowledge/import-server", handle_knowledge_import_server)
    app.router.add_post("/api/knowledge/upload", handle_knowledge_upload)
    app.router.add_get("/api/knowledge/{slug}", handle_knowledge_get)
    app.router.add_delete("/api/knowledge/{slug}", handle_knowledge_delete_app)
    app.router.add_post("/api/knowledge/{slug}/facts", handle_knowledge_add)
    app.router.add_delete("/api/knowledge/{slug}/facts/{index}", handle_knowledge_delete_fact)
