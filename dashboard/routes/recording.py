"""Screenbox Dashboard -- recording API route handlers.

Record control (start/stop/status) proxies to MCP HTTP API.
File listing, streaming, and deletion use local filesystem (ro mount).
"""

import os

from aiohttp import web

from config import _VALID_ID
import mcp_client

# Recordings: shared volume at /data/screenbox/desktops/{id}/recordings/
# Fallback: old path /data/recordings or ~/.screenbox/recordings
_SHARED_VOL = "/data/screenbox/desktops"
_OLD_REC = "/data/recordings"
_HOME_REC = os.path.expanduser("~/.screenbox/recordings")
RECORDINGS_BASE = os.environ.get("SCREENBOX_RECORDINGS_DIR",
                                  _OLD_REC if os.path.isdir(_OLD_REC)
                                  else _HOME_REC)


def _find_rec_dir(desktop_id: str) -> str | None:
    """Find recordings directory for desktop (new shared vol or old path)."""
    # New: shared volume dossier
    new_path = os.path.join(_SHARED_VOL, desktop_id, "recordings")
    if os.path.isdir(new_path):
        return new_path
    # Old: bind mount or host path
    old_path = os.path.join(RECORDINGS_BASE, desktop_id)
    if os.path.isdir(old_path):
        return old_path
    return None


async def handle_api_recordings(request: web.Request) -> web.Response:
    """List recordings for a desktop."""
    desktop_id = request.query.get("id", "")
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop_id"}, status=400)
    rec_dir = _find_rec_dir(desktop_id)
    if not rec_dir:
        return web.json_response([])
    recs = []
    for f in sorted(os.listdir(rec_dir), reverse=True):
        if f.endswith(".mp4"):
            path = os.path.join(rec_dir, f)
            stat = os.stat(path)
            recs.append({
                "file": f,
                "size_mb": round(stat.st_size / 1024 / 1024, 1),
                "created": stat.st_mtime,
            })
    return web.json_response(recs)


async def handle_api_recording_stream(request: web.Request) -> web.Response:
    """Stream a recording for HTML5 video player."""
    desktop_id = request.query.get("id", "")
    filename = request.query.get("file", "")
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.Response(status=400, text="Invalid desktop_id")
    if not filename or ".." in filename or "/" in filename:
        return web.Response(status=400, text="Invalid filename")
    rec_dir = _find_rec_dir(desktop_id)
    if not rec_dir:
        return web.Response(status=404, text="Not found")
    path = os.path.join(rec_dir, filename)
    if not os.path.isfile(path):
        return web.Response(status=404, text="Not found")

    # Support Range requests for seeking
    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")

    if range_header:
        # Parse Range: bytes=start-end
        range_spec = range_header.replace("bytes=", "")
        parts = range_spec.split("-")
        start = int(parts[0]) if parts[0] else 0
        end = int(parts[1]) if parts[1] else file_size - 1
        length = end - start + 1

        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(length)

        return web.Response(
            body=data,
            status=206,
            headers={
                "Content-Type": "video/mp4",
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Content-Length": str(length),
                "Accept-Ranges": "bytes",
            },
        )

    # Full file
    with open(path, "rb") as f:
        data = f.read()
    return web.Response(
        body=data,
        content_type="video/mp4",
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )


async def handle_api_record_control(request: web.Request) -> web.Response:
    """Start or stop recording via MCP API."""
    body = await request.json()
    desktop_id = body.get("id", "").strip()
    action = body.get("action", "").strip()
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop_id"}, status=400)
    if action not in ("start", "stop"):
        return web.json_response({"error": "action must be start or stop"}, status=400)

    result = await mcp_client.record_control(desktop_id, action)
    status = 200 if not result.get("error") else 400
    return web.json_response(result, status=status)


async def handle_api_recording_status(request: web.Request) -> web.Response:
    """Check recording status via MCP API."""
    desktop_id = request.query.get("id", "")
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop_id"}, status=400)

    result = await mcp_client.record_status(desktop_id)
    # MCP returns {recording: bool, pid: int|null}
    # Dashboard JS expects {available: bool, recording: bool}
    if "error" not in result:
        result["available"] = True
    else:
        result["available"] = False
    return web.json_response(result)


async def handle_api_recording_delete(request: web.Request) -> web.Response:
    """Delete a recording."""
    body = await request.json()
    desktop_id = body.get("id", "").strip()
    filename = body.get("file", "").strip()
    if not desktop_id or not _VALID_ID.match(desktop_id):
        return web.json_response({"error": "Invalid desktop_id"}, status=400)
    if not filename or ".." in filename or "/" in filename:
        return web.json_response({"error": "Invalid filename"}, status=400)
    rec_dir = _find_rec_dir(desktop_id)
    if not rec_dir:
        return web.json_response({"error": "Not found"}, status=404)
    path = os.path.join(rec_dir, filename)
    if os.path.isfile(path):
        os.remove(path)
        return web.json_response({"ok": True, "deleted": filename})
    return web.json_response({"error": "Not found"}, status=404)


def setup(app: web.Application):
    """Register recording API routes."""
    app.router.add_get("/api/recordings", handle_api_recordings)
    app.router.add_get("/api/recording/status", handle_api_recording_status)
    app.router.add_get("/api/recording/stream", handle_api_recording_stream)
    app.router.add_post("/api/record", handle_api_record_control)
    app.router.add_post("/api/recording/delete", handle_api_recording_delete)
