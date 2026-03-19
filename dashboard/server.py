#!/usr/bin/env python3
"""Screenbox Dashboard -- lightweight web UI for managing desktops.

Single-port architecture: HTTP + WebSocket on :16000.
No hardcoded ports in frontend -- works behind any reverse proxy.
"""

import asyncio
import hmac
import time

from aiohttp import web, WSMsgType

from config import (
    _VALID_ID, PORT, HOST, API_TOKEN, DASHBOARD_AUTH, _MIME_MAP, _APP_DIR,
)
from state import get_desktops, subscribe, unsubscribe
from mcp_client import get_container_ip_sync as _get_container_ip
import mcp_events
import routes


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _check_auth(request: web.Request) -> bool:
    """Verify auth via Bearer header or session cookie."""
    if not API_TOKEN:
        return True
    # 1. Bearer header (API clients)
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].encode(), API_TOKEN.encode()):
        return True
    # 2. Session cookie (browser)
    cookie = request.cookies.get("sb_token", "")
    if cookie and hmac.compare_digest(cookie.encode(), API_TOKEN.encode()):
        return True
    return False


@web.middleware
async def auth_middleware(request: web.Request, handler):
    if not API_TOKEN or DASHBOARD_AUTH == "none":
        return await handler(request)

    # Login: ?token= in URL sets cookie. For page requests: redirect to clean URL.
    # For API requests: set cookie and continue (so fetch with ?token= works).
    qt = request.query.get("token", "")
    if qt and hmac.compare_digest(qt.encode(), API_TOKEN.encode()):
        if request.path.startswith(("/api/", "/ws/")):
            resp = await handler(request)
            resp.set_cookie("sb_token", API_TOKEN, httponly=True, samesite="Strict", max_age=86400 * 30)
            return resp
        else:
            resp = web.HTTPFound(request.path)
            resp.set_cookie("sb_token", API_TOKEN, httponly=True, samesite="Strict", max_age=86400 * 30)
            return resp

    # Public share pages and share VNC proxy -- skip auth
    if request.path.startswith("/s/") or request.path.startswith("/share-vnc/"):
        return await handler(request)

    # Public share validation endpoint -- skip auth
    if request.path == "/api/desktop/share/validate":
        return await handler(request)

    # "auto" mode: browser page requests get a session cookie automatically.
    # The token still protects API/WS endpoints from unauthenticated scripts.
    if DASHBOARD_AUTH == "auto" and not _check_auth(request):
        if not request.path.startswith(("/api/", "/ws/", "/vnc/", "/rdp/")):
            # Browser visit -- set cookie and let through
            resp = await handler(request)
            resp.set_cookie("sb_token", API_TOKEN, httponly=True, samesite="Strict", max_age=86400 * 30)
            return resp

    # Auth check for API, WebSocket, and VNC/RDP proxy endpoints
    if request.path.startswith(("/api/", "/ws/", "/vnc/", "/rdp/")):
        if not _check_auth(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

    return await handler(request)


# ---------------------------------------------------------------------------
# Error page
# ---------------------------------------------------------------------------


def _error_page(code: int, title: str, detail: str = "") -> web.Response:
    """Return a branded Screenbox error page."""
    from html import escape
    title, detail = escape(title), escape(detail)
    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Screenbox</title>
<style>
* {{ margin: 0; padding: 0; }}
body {{ background: #0a0e14; color: #d0d4dc; font-family: -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif;
  display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; gap: 16px; }}
.icon {{ width: 48px; height: 48px; border: 2px solid #1a3a7a; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; }}
.icon svg {{ width: 20px; height: 20px; }}
h2 {{ font-size: 16px; font-weight: 400; letter-spacing: 1px; }}
p {{ color: #606878; font-size: 12px; font-weight: 300; letter-spacing: 0.5px; }}
.code {{ color: #1a3a7a; font-size: 11px; font-family: monospace; }}
a {{ margin-top: 8px; background: none; border: 1px solid #1a3a7a; color: #d0d4dc;
  padding: 8px 24px; border-radius: 4px; font-size: 12px; letter-spacing: 1px;
  cursor: pointer; font-weight: 300; text-decoration: none;
  transition: background 0.15s, border-color 0.15s; }}
a:hover {{ background: rgba(26,58,122,0.3); border-color: #2a5aaa; }}
</style></head><body>
<div class="icon"><svg viewBox="0 0 24 24" fill="none" stroke="#1a3a7a" stroke-width="2" stroke-linecap="round">
<circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
</svg></div>
<h2>{title}</h2>
<p>{detail}</p>
<span class="code">{code}</span>
<a href="/">Back to dashboard</a>
</body></html>"""
    return web.Response(text=body, status=code, content_type="text/html")


# ---------------------------------------------------------------------------
# WebSocket proxy: browser <-> container VNC/RDP
# ---------------------------------------------------------------------------


async def handle_ws_proxy(request: web.Request) -> web.WebSocketResponse:
    """WebSocket-to-TCP proxy for VNC and RDP.

    Routes:
      /vnc/{desktop_id} -> container:5900 (VNC)
      /rdp/{desktop_id} -> container:3389 (RDP)
    """
    # Determine protocol from URL path prefix
    proto = "rdp" if request.path.startswith("/rdp/") else "vnc"
    desktop_id = request.match_info["desktop_id"]
    target_port = 3389 if proto == "rdp" else 5900

    if not _VALID_ID.match(desktop_id):
        return _error_page(400, "Invalid desktop ID")

    container_ip = _get_container_ip(desktop_id)
    if not container_ip:
        print(f"[{proto}-proxy] {desktop_id} container IP not found", flush=True)
        return _error_page(404, "Desktop not found")

    print(f"[{proto}-proxy] {desktop_id} -> {container_ip}:{target_port}", flush=True)

    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024, heartbeat=30.0)
    await ws.prepare(request)

    t0 = time.time()
    stats = {"ws2tcp": 0, "tcp2ws": 0, "bytes_in": 0, "bytes_out": 0}
    tcp_writer = None

    try:
        tcp_reader, tcp_writer = await asyncio.wait_for(
            asyncio.open_connection(container_ip, target_port), timeout=5)
        print(f"[{proto}-proxy] {desktop_id} TCP CONNECTED", flush=True)

        stop = asyncio.Event()

        async def ws_to_tcp():
            try:
                async for msg in ws:
                    if stop.is_set():
                        break
                    if msg.type == WSMsgType.BINARY:
                        stats["ws2tcp"] += 1
                        stats["bytes_in"] += len(msg.data)
                        tcp_writer.write(msg.data)
                        await tcp_writer.drain()
                    elif msg.type == WSMsgType.TEXT:
                        data = msg.data.encode()
                        stats["ws2tcp"] += 1
                        stats["bytes_in"] += len(data)
                        tcp_writer.write(data)
                        await tcp_writer.drain()
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
            except Exception as e:
                print(f"[{proto}-proxy] {desktop_id} WS ERROR: {e}", flush=True)
            finally:
                stop.set()

        async def tcp_to_ws():
            try:
                while not stop.is_set():
                    data = await tcp_reader.read(65536)
                    if not data:
                        print(f"[{proto}-proxy] {desktop_id} TCP EOF", flush=True)
                        break
                    stats["tcp2ws"] += 1
                    stats["bytes_out"] += len(data)
                    await ws.send_bytes(data)
            except Exception as e:
                print(f"[{proto}-proxy] {desktop_id} TCP ERROR: {e}", flush=True)
            finally:
                stop.set()

        await asyncio.gather(ws_to_tcp(), tcp_to_ws())

    except ConnectionRefusedError:
        print(f"[{proto}-proxy] {desktop_id} REFUSED on port {target_port}", flush=True)
        await ws.close(code=4502, message=f"{proto.upper()} connection refused".encode())
    except asyncio.TimeoutError:
        print(f"[{proto}-proxy] {desktop_id} TIMEOUT connecting", flush=True)
        await ws.close(code=4504, message=f"{proto.upper()} connection timeout".encode())
    except Exception as e:
        print(f"[{proto}-proxy] {desktop_id} ERROR: {type(e).__name__}: {e}", flush=True)
    finally:
        if tcp_writer:
            tcp_writer.close()
        elapsed = time.time() - t0
        print(f"[{proto}-proxy] {desktop_id} SESSION END: {elapsed:.1f}s "
              f"ws2tcp={stats['ws2tcp']} tcp2ws={stats['tcp2ws']} "
              f"in={stats['bytes_in']} out={stats['bytes_out']}", flush=True)

    return ws


# ---------------------------------------------------------------------------
# WebSocket event stream: real-time desktop state changes
# ---------------------------------------------------------------------------


async def handle_ws_events(request: web.Request) -> web.WebSocketResponse:
    """Push desktop events (create, destroy, pause, resume) to browser.

    Clients connect here for real-time updates. Falls back to polling
    if WebSocket disconnects.
    """
    ws = web.WebSocketResponse(heartbeat=30.0)
    await ws.prepare(request)

    q = subscribe()
    print("[ws-events] client connected", flush=True)

    try:
        # Send initial state immediately
        desktops = get_desktops()
        await ws.send_json({"event": "init", "desktops": desktops, "ts": time.time()})

        async def _queue_reader():
            """Forward events from queue to WebSocket."""
            while not ws.closed:
                try:
                    msg = await q.get()
                    if ws.closed:
                        break
                    await ws.send_json(msg)
                except (ConnectionResetError, ConnectionError):
                    break

        async def _ws_reader():
            """Read WS messages (handles pong/close internally by aiohttp)."""
            async for msg in ws:
                if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                    break
                # Ignore other messages (TEXT/BINARY) from client

        # Run both concurrently -- when either finishes, cancel the other
        done, pending = await asyncio.wait(
            [asyncio.create_task(_queue_reader()),
             asyncio.create_task(_ws_reader())],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()

    except Exception as e:
        print(f"[ws-events] error: {e}", flush=True)
    finally:
        unsubscribe(q)
        print("[ws-events] client disconnected", flush=True)

    return ws


# ---------------------------------------------------------------------------
# Static file handlers
# ---------------------------------------------------------------------------


_BOOT_TS = str(int(time.time()))  # unique per server start

async def handle_index(request: web.Request) -> web.Response:
    html = (_APP_DIR / "index.html").read_text()
    # Cache-bust all static scripts/styles: append ?_t=<boot_timestamp>
    import re as _re
    html = _re.sub(
        r'(src|href)="(/static/[^"]+?)(?:\?[^"]*)?(")',
        rf'\1="\2?_t={_BOOT_TS}\3',
        html,
    )
    return web.Response(
        text=html, content_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def handle_view(request: web.Request) -> web.Response:
    body = (_APP_DIR / "view.html").read_bytes()
    return web.Response(
        body=body, content_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


async def handle_share(request: web.Request) -> web.Response:
    """Serve share viewer page. Token validated client-side via /api/desktop/share/validate."""
    share_page = _APP_DIR / "share.html"
    if not share_page.is_file():
        return _error_page(500, "Share page not found")
    body = share_page.read_bytes()
    return web.Response(
        body=body, content_type="text/html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


def _rfb_filter_viewonly(data: bytes) -> bytes:
    """Filter RFB client-to-server messages: allow only view-related, drop input.

    RFB client message types:
      0 = SetPixelFormat     -- OK (display config)
      2 = SetEncodings       -- OK (display config)
      3 = FramebufferUpdateRequest -- OK (request screen data)
      4 = KeyEvent           -- DROP (keyboard input)
      5 = PointerEvent       -- DROP (mouse input)
      6 = ClientCutText      -- DROP (clipboard write)

    During RFB handshake (first ~20 messages), pass everything through
    since the protocol negotiation uses raw bytes, not typed messages.
    """
    if not data:
        return data
    msg_type = data[0]
    # Allow display-related messages, block input messages
    if msg_type in (4, 5, 6):
        return b""  # silently drop
    return data


async def handle_share_vnc_proxy(request: web.Request) -> web.WebSocketResponse:
    """WebSocket VNC proxy for share viewers -- validates share token, view-only.

    Security: RFB protocol filtering enforced server-side. Input events
    (keyboard, mouse, clipboard) are dropped even if client sends them.
    Token is re-validated every 30s -- revocation takes effect promptly.
    """
    token = request.match_info["token"]

    # Validate share token via MCP API
    from mcp_client import validate_share_token
    share = validate_share_token(token)
    if not share:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close(code=4403, message=b"Invalid or expired share link")
        return ws

    desktop_id = share["desktop_id"]
    container_ip = _get_container_ip(desktop_id)
    if not container_ip:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.close(code=4404, message=b"Desktop not found")
        return ws

    print(f"[share-vnc] {desktop_id} (token={token[:8]}...) -> {container_ip}:5900", flush=True)

    ws = web.WebSocketResponse(max_msg_size=4 * 1024 * 1024, heartbeat=30.0)
    await ws.prepare(request)

    t0 = time.time()
    tcp_writer = None
    # RFB handshake: first few messages are protocol negotiation, not typed
    handshake_done = False
    handshake_msgs = 0

    try:
        tcp_reader, tcp_writer = await asyncio.wait_for(
            asyncio.open_connection(container_ip, 5900), timeout=5)

        stop = asyncio.Event()

        async def ws_to_tcp():
            nonlocal handshake_done, handshake_msgs
            try:
                async for msg in ws:
                    if stop.is_set():
                        break
                    if msg.type == WSMsgType.BINARY:
                        data = msg.data
                        # Pass handshake through (RFB version, security, init)
                        if not handshake_done:
                            handshake_msgs += 1
                            if handshake_msgs > 6:
                                handshake_done = True
                        if handshake_done:
                            data = _rfb_filter_viewonly(data)
                        if data:
                            tcp_writer.write(data)
                            await tcp_writer.drain()
                    elif msg.type == WSMsgType.TEXT:
                        # noVNC sometimes sends text during handshake
                        data = msg.data.encode()
                        if handshake_done:
                            data = _rfb_filter_viewonly(data)
                        if data:
                            tcp_writer.write(data)
                            await tcp_writer.drain()
                    elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
                        break
            except Exception:
                pass
            finally:
                stop.set()

        async def tcp_to_ws():
            try:
                while not stop.is_set():
                    data = await tcp_reader.read(65536)
                    if not data:
                        break
                    await ws.send_bytes(data)
            except Exception:
                pass
            finally:
                stop.set()

        async def expiry_watchdog():
            """Re-validate share token every 30s. Close if expired/revoked."""
            try:
                while not stop.is_set():
                    await asyncio.sleep(30)
                    if stop.is_set():
                        break
                    valid = validate_share_token(token)
                    if not valid:
                        print(f"[share-vnc] {desktop_id} token expired/revoked, closing", flush=True)
                        stop.set()
                        try:
                            await ws.close(code=4410, message=b"Share link expired")
                        except Exception:
                            pass
                        break
            except asyncio.CancelledError:
                pass

        await asyncio.gather(ws_to_tcp(), tcp_to_ws(), expiry_watchdog())

    except (ConnectionRefusedError, asyncio.TimeoutError) as e:
        await ws.close(code=4502, message=str(e).encode()[:120])
    except Exception as e:
        print(f"[share-vnc] error: {e}", flush=True)
    finally:
        if tcp_writer:
            tcp_writer.close()
        elapsed = time.time() - t0
        print(f"[share-vnc] {desktop_id} session end: {elapsed:.1f}s", flush=True)

    return ws


async def handle_novnc(request: web.Request) -> web.Response:
    """Serve noVNC static files with path traversal protection."""
    rel_path = request.match_info["path"]
    file_path = (_APP_DIR / "novnc" / rel_path).resolve()
    if not file_path.is_relative_to(_APP_DIR / "novnc"):
        return web.Response(status=403, text="Forbidden")
    if not file_path.is_file():
        return web.Response(status=404, text="Not found")
    ext = file_path.suffix.lower()
    content_type = _MIME_MAP.get(ext, "application/octet-stream")
    body = file_path.read_bytes()
    return web.Response(
        body=body, content_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


async def handle_static(request: web.Request) -> web.Response:
    """Serve static files from static/ directory with path traversal protection."""
    rel_path = request.match_info["path"]
    file_path = (_APP_DIR / "static" / rel_path).resolve()
    if not file_path.is_relative_to(_APP_DIR / "static"):
        return web.Response(status=403, text="Forbidden")
    if not file_path.is_file():
        return web.Response(status=404, text="Not found")
    ext = file_path.suffix.lower()
    content_type = _MIME_MAP.get(ext, "application/octet-stream")
    body = file_path.read_bytes()
    return web.Response(
        body=body, content_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    # Cache will be populated by MCP events consumer on startup

    if not API_TOKEN:
        if DASHBOARD_AUTH != "none":
            print("[screenbox-dashboard] WARNING: SCREENBOX_API_TOKEN not set. "
                  "Dashboard auth is disabled. Set SCREENBOX_API_TOKEN or "                  "SCREENBOX_DASHBOARD_AUTH=none to suppress this warning.", flush=True)

    # Build aiohttp app
    app = web.Application(middlewares=[auth_middleware])

    # API routes (from route modules)
    routes.setup(app)

    # WebSocket: real-time events for dashboard
    app.router.add_get("/ws/events", handle_ws_events)

    # WebSocket proxy (VNC + RDP) -- proto determined by path prefix in handler
    app.router.add_get("/vnc/{desktop_id}", handle_ws_proxy)
    app.router.add_get("/rdp/{desktop_id}", handle_ws_proxy)

    # Static pages
    app.router.add_get("/", handle_index)
    app.router.add_get("/index.html", handle_index)
    app.router.add_get("/view", handle_view)

    # Share: public view-only access
    app.router.add_get("/s/{token}", handle_share)
    app.router.add_get("/share-vnc/{token}", handle_share_vnc_proxy)

    # noVNC static files
    app.router.add_get("/novnc/{path:.*}", handle_novnc)

    # Static files (CSS, JS, images)
    app.router.add_get("/static/{path:.*}", handle_static)

    async def start_background_tasks(app_):
        app_["mcp_events"] = asyncio.create_task(mcp_events.mcp_events_consumer())
        app_["screenshot_loop"] = asyncio.create_task(mcp_events._screenshot_loop())
        app_["resource_collector"] = asyncio.create_task(mcp_events.resource_collector())

    async def stop_background_tasks(app_):
        for key in ("mcp_events", "screenshot_loop", "resource_collector"):
            task = app_.get(key)
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    app.on_startup.append(start_background_tasks)
    app.on_cleanup.append(stop_background_tasks)

    print(f"[screenbox-dashboard] http://{HOST}:{PORT}", flush=True)
    web.run_app(app, host=HOST, port=PORT, print=None)


if __name__ == "__main__":
    main()
