"""Screenbox HTTP API -- REST endpoints for dashboard integration.

These endpoints wrap the existing manager/desktop methods,
providing a single control plane for all desktop operations.
Dashboard should call these instead of docker exec directly.

All endpoints require Bearer token auth (SCREENBOX_API_TOKEN env).
"""

import asyncio
import collections
import json
import hmac
import httpx
import logging
import os
import subprocess
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

log = logging.getLogger("screenbox.http_api")

# Simple in-memory rate limiter: max attempts per IP per window
_rate_limits: dict[str, collections.deque] = {}
_RATE_WINDOW = 60  # seconds
_RATE_MAX = 10  # max attempts per window


def _rate_check(ip: str) -> bool:
    """Return True if request is allowed, False if rate-limited."""
    now = time.time()
    if ip not in _rate_limits:
        _rate_limits[ip] = collections.deque()
    q = _rate_limits[ip]
    while q and q[0] < now - _RATE_WINDOW:
        q.popleft()
    if len(q) >= _RATE_MAX:
        return False
    q.append(now)
    return True

def _read_jsonl_tail(path: str, max_bytes: int = 512 * 1024) -> list[dict]:
    """Read last max_bytes of a JSONL file, return parsed entries."""
    entries = []
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            read_size = min(size, max_bytes)
            f.seek(size - read_size)
            chunk = f.read().decode("utf-8", errors="replace")
        for line in chunk.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return entries


_API_TOKEN = os.environ.get("SCREENBOX_API_TOKEN", "")
_TRANSPORT = os.environ.get("SCREENBOX_TRANSPORT", "stdio")
if _API_TOKEN:
    log.info("HTTP API auth: Bearer token configured")
elif _TRANSPORT == "stdio":
    log.info("HTTP API auth: no token (stdio mode, local only)")
else:
    raise RuntimeError(
        "SCREENBOX_API_TOKEN must be set for network transports (sse/http). "
        "Set SCREENBOX_API_TOKEN env var or use stdio transport."
    )


def _check_auth(request: Request) -> bool:
    """Verify Bearer token (Authorization header only)."""
    if not _API_TOKEN:
        return True  # stdio mode, local only
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return hmac.compare_digest(auth[7:].encode(), _API_TOKEN.encode())
    return False


def _auth_error():
    return JSONResponse({"error": "Unauthorized"}, status_code=401)


def _parse_mem(s: str) -> float:
    """Parse docker memory string like '256MiB', '2GiB', '1.5GB' to MB."""
    s = s.strip()
    try:
        if "GiB" in s or "GB" in s:
            return float(s.replace("GiB", "").replace("GB", "").strip()) * 1024
        if "MiB" in s or "MB" in s:
            return float(s.replace("MiB", "").replace("MB", "").strip())
        if "KiB" in s or "KB" in s:
            return float(s.replace("KiB", "").replace("KB", "").strip()) / 1024
    except ValueError:
        pass
    return 0.0


def register(mcp):
    """Register all HTTP API routes on the FastMCP server."""
    from .globals import manager, get_desktop, registry, guard
    from .events import event_bus

    # -- Desktop lifecycle --

    @mcp.custom_route("/api/desktop/list", methods=["GET"])
    async def api_desktop_list(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        assignments = {a["desktop_id"]: a for a in registry.list_assignments()}
        desktops = []
        for did, info in manager._desktops.items():
            a = assignments.get(did, {})
            desktops.append({
                "id": did,
                "name": f"screenbox-{did}",
                "state": info.state.value if hasattr(info.state, 'value') else str(info.state),
                "label": info.label,
                "created_at": info.created_at,
                "last_tool_call": info.last_tool_call,
                "rdp_port": info.rdp_port,
                "vnc_port": info.vnc_port,
                "ws_port": info.ws_port,
                "novnc_port": info.rdp_port,
                "resolution": f"{info.screen_width}x{info.screen_height}",
                "image": info.image,
                "acquired_by": info.acquired_by,
                "acquired_at": info.acquired_at,
                "assigned_to": a.get("agent_id"),
                "assigned_name": a.get("display_name"),
            })
        return JSONResponse(desktops)

    @mcp.custom_route("/api/desktop/status", methods=["GET"])
    async def api_desktop_status(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        did = request.query_params.get("id", "")
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        info = manager._desktops.get(did)
        if not info:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse({
            "id": did,
            "state": info.state.value if hasattr(info.state, 'value') else str(info.state),
            "label": info.label,
            "created_at": info.created_at,
            "last_tool_call": info.last_tool_call,
            "rdp_port": info.rdp_port,
            "vnc_port": info.vnc_port,
            "ws_port": info.ws_port,
            "acquired_by": info.acquired_by,
            "acquired_at": info.acquired_at,
        })

    @mcp.custom_route("/api/desktop/stats", methods=["GET"])
    async def api_desktop_stats(request: Request) -> Response:
        """Per-desktop resource stats via cgroup files inside containers."""
        if not _check_auth(request):
            return _auth_error()

        def _get_stats(desktop_id: str) -> dict:
            """Read cgroup v1/v2 stats + disk usage from inside container."""
            try:
                result = manager.exec(desktop_id, [
                    "bash", "-c",
                    # cgroup v2 first, fallback to v1
                    'MEM_V2=$(cat /sys/fs/cgroup/memory.current 2>/dev/null);'
                    'MEM_V1=$(cat /sys/fs/cgroup/memory/memory.usage_in_bytes 2>/dev/null);'
                    'echo "MEM_USED=${MEM_V2:-${MEM_V1:-0}}";'
                    'MAX_V2=$(cat /sys/fs/cgroup/memory.max 2>/dev/null);'
                    'MAX_V1=$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null);'
                    'echo "MEM_MAX=${MAX_V2:-${MAX_V1:-0}}";'
                    'CPU_V2=$(grep usage_usec /sys/fs/cgroup/cpu.stat 2>/dev/null | cut -d" " -f2);'
                    'CPU_V1=$(cat /sys/fs/cgroup/cpuacct/cpuacct.usage 2>/dev/null);'
                    'echo "CPU_USEC=${CPU_V2:-${CPU_V1:-0}}";'
                    'echo "DISK_MB=$(du -sm /home 2>/dev/null | cut -f1 || echo 0)"'
                ], timeout=5)
                stdout = result.stdout.decode() if result.stdout else ""
                vals = {}
                for line in stdout.strip().split("\n"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        vals[k] = v.strip()
                mem_used = int(vals.get("MEM_USED", "0"))
                mem_max = int(vals.get("MEM_MAX", "0"))
                if mem_max > 10**15:  # "max" = unlimited
                    mem_max = 0
                return {
                    "total_rss_mb": round(mem_used / 1048576, 1),
                    "mem_limit_mb": round(mem_max / 1048576),
                    "disk_total_mb": int(vals.get("DISK_MB", "0")),
                    "disk_quota_mb": 2048,
                    "host_cores": os.cpu_count() or 1,
                }
            except Exception:
                return {}

        try:
            tasks = {}
            for did, info in manager._desktops.items():
                if info.state.value == "running":
                    tasks[did] = asyncio.to_thread(_get_stats, did)
            if not tasks:
                return JSONResponse({})
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)
            data = {}
            for did, result in zip(tasks.keys(), results):
                if isinstance(result, dict) and result:
                    data[did] = result
            return JSONResponse(data)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/desktop/create", methods=["POST"])
    async def api_desktop_create(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        did = body.get("id", "").strip()
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        try:
            # Load profile if specified
            profile_name = body.get("profile", "")
            profile_json = None
            if profile_name:
                import pathlib, json as _json
                ppath = pathlib.Path(f"/data/screenbox/profiles/{profile_name}.json")
                if ppath.exists():
                    profile_data = _json.loads(ppath.read_text())
                    profile_json = _json.dumps(profile_data, indent=2)
                    # Apply resolution from profile if not explicitly set
                    if not body.get("resolution") and "container" in profile_data:
                        c = profile_data["container"]
                        if c.get("screen_width") and c.get("screen_height"):
                            body["resolution"] = f"{c['screen_width']}x{c['screen_height']}"
            result = manager.create(
                did,
                label=body.get("label", ""),
                url=body.get("url", ""),
                resolution=body.get("resolution", ""),
                image=body.get("image", ""),
                profile_json=profile_json,
                profile_name=profile_name or None,
            )
            return JSONResponse({"ok": True, "desktop_id": did, "state": "running"})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/desktop/destroy", methods=["POST"])
    async def api_desktop_destroy(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        did = body.get("id", "").strip()
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        try:
            save = body.get("auto_snapshot", body.get("save_snapshot", True))
            manager.destroy(did, auto_snapshot=save)
            return JSONResponse({"ok": True, "desktop_id": did, "destroyed": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/desktop/delete-data", methods=["POST"])
    async def api_desktop_delete_data(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        did = body.get("id", "").strip()
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        try:
            deleted = manager.delete_data(did)
            return JSONResponse({"ok": True, "desktop_id": did, "deleted": deleted})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/desktop/control", methods=["POST"])
    async def api_desktop_control(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        did = body.get("id", "").strip()
        action = body.get("action", "").strip()
        if not did or not action:
            return JSONResponse({"error": "Missing id or action"}, status_code=400)
        if action not in ("pause", "resume", "unpause", "stop", "start"):
            return JSONResponse({"error": f"Invalid action: {action}"}, status_code=400)
        try:
            # Map to manager methods
            if action == "pause":
                manager.pause(did)
            elif action in ("resume", "unpause"):
                manager.resume(did)
            elif action == "stop":
                manager.stop(did)
            elif action == "start":
                manager.start(did)
            return JSONResponse({"ok": True, "desktop_id": did, "action": action})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # -- Overlay --

    @mcp.custom_route("/api/desktop/overlay", methods=["POST"])
    async def api_desktop_overlay(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        did = body.get("id", "").strip()
        text = body.get("text", "").strip()
        if not did or not text:
            return JSONResponse({"error": "Missing id or text"}, status_code=400)
        try:
            d = get_desktop(did)
            # Parse "enabled=1,cursor=1,dots=1,trail=0"
            parts = dict(p.split("=") for p in text.replace("\n", ",").split(",") if "=" in p)
            result = d.overlay_mode(
                enabled=parts.get("enabled", "1") == "1",
                cursor=parts.get("cursor", "1") == "1",
                dots=parts.get("dots", "1") == "1",
                trail=parts.get("trail", "0") == "1",
            )
            return JSONResponse({"ok": True, **result})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # -- Recording --

    @mcp.custom_route("/api/desktop/record", methods=["GET", "POST"])
    async def api_desktop_record(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()

        if request.method == "GET":
            # Status check
            did = request.query_params.get("id", "")
            if not did:
                return JSONResponse({"error": "Missing id"}, status_code=400)
            try:
                d = get_desktop(did)
                status = d.record_status()
                return JSONResponse(status)
            except Exception as e:
                return JSONResponse({"error": str(e)}, status_code=500)

        # POST: start or stop
        body = await request.json()
        did = body.get("id", "").strip()
        action = body.get("action", "").strip()
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        if action not in ("start", "stop"):
            return JSONResponse({"error": "action must be start or stop"}, status_code=400)
        try:
            d = get_desktop(did)
            if action == "start":
                result = d.record_start()
                event_bus.emit("recording_started", did)
            else:
                result = d.record_stop()
                event_bus.emit("recording_stopped", did)
            return JSONResponse(result)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # -- Recordings list --

    @mcp.custom_route("/api/desktop/recordings", methods=["GET"])
    async def api_desktop_recordings(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        did = request.query_params.get("id", "")
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        rec_dir = os.path.expanduser(f"~/.screenbox/recordings/{did}")
        if not os.path.isdir(rec_dir):
            return JSONResponse([])
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
        return JSONResponse(recs)

    # -- Screenshot --

    @mcp.custom_route("/api/desktop/screenshot", methods=["GET"])
    async def api_desktop_screenshot(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        did = request.query_params.get("id", "")
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        info = manager._desktops.get(did)
        if not info:
            return Response(status_code=404, content="Desktop not found")
        try:
            from .desktop import Desktop, DISPLAY
            from .globals import get_logger
            d = Desktop(manager, did, action_logger=get_logger(did))
            img_bytes = d.screenshot(quality=80, _log=False)
            if img_bytes:
                return Response(content=img_bytes, media_type="image/jpeg")
            return Response(status_code=503, content="Screenshot unavailable")
        except Exception as e:
            return Response(status_code=503, content=str(e))

    # -- Container IP (for VNC proxy) --

    @mcp.custom_route("/api/desktop/ip", methods=["GET"])
    async def api_desktop_ip(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        did = request.query_params.get("id", "")
        if not did:
            return JSONResponse({"error": "Missing id"}, status_code=400)
        if did not in manager._desktops:
            return JSONResponse({"error": "Not found"}, status_code=404)
        import subprocess as _sp
        container = f"screenbox-{did}"
        try:
            r = _sp.run(
                ["docker", "inspect", "-f",
                 "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}={{.IPAddress}} {{end}}",
                 container],
                capture_output=True, text=True, timeout=5,
            )
            pairs = {}
            for token in r.stdout.strip().split():
                if "=" in token:
                    net, ip = token.split("=", 1)
                    if ip:
                        pairs[net] = ip
            # Prefer configured network
            net = manager.config.docker_network
            ip = pairs.get(net) if net else None
            if not ip:
                ip = next(iter(pairs.values()), None)
            if ip:
                return JSONResponse({"id": did, "ip": ip, "networks": pairs})
            return JSONResponse({"error": "No IP found"}, status_code=404)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # -- Input (xdotool proxy) --

    @mcp.custom_route("/api/desktop/input", methods=["POST"])
    async def api_desktop_input(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        did = body.get("id", "").strip()
        action = body.get("action", "").strip()
        if not did or not action:
            return JSONResponse({"error": "Missing id or action"}, status_code=400)
        if did not in manager._desktops:
            return JSONResponse({"error": "Not found"}, status_code=404)

        import re as _re
        import subprocess as _sp
        container = f"screenbox-{did}"

        def _xdotool(args):
            r = _sp.run(
                ["docker", "exec", "-e", "DISPLAY=:99", container,
                 "xdotool"] + args,
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode != 0:
                return {"error": r.stderr.strip() or f"xdotool failed (rc={r.returncode})"}
            return {"ok": True, "action": action}

        try:
            if action == "mousemove":
                x, y = int(body["x"]), int(body["y"])
                return JSONResponse(_xdotool(["mousemove", str(x), str(y)]))
            elif action == "click":
                x, y = int(body["x"]), int(body["y"])
                btn = str(int(body.get("button", 1)))
                for args in [["mousemove", str(x), str(y)],
                             ["mousedown", btn], ["mouseup", btn]]:
                    r = _xdotool(args)
                    if "error" in r:
                        return JSONResponse(r, status_code=400)
                return JSONResponse({"ok": True, "action": action})
            elif action in ("mousedown", "mouseup"):
                btn = str(int(body.get("button", 1)))
                return JSONResponse(_xdotool([action, btn]))
            elif action == "type":
                text = body.get("text", "")
                if not text:
                    return JSONResponse({"error": "Missing text"}, status_code=400)
                return JSONResponse(_xdotool(["type", "--clearmodifiers", "--", text]))
            elif action == "key":
                combo = body.get("combo", "")
                if not combo or not _re.match(r'^[a-zA-Z0-9+_\-]+$', combo):
                    return JSONResponse({"error": "Invalid key combo"}, status_code=400)
                return JSONResponse(_xdotool(["key", combo]))
            elif action == "scroll":
                x, y = int(body["x"]), int(body["y"])
                direction = body.get("direction", "down")
                clicks = int(body.get("clicks", 3))
                btn = str(5 if direction == "down" else 4)
                r = _xdotool(["mousemove", str(x), str(y)])
                if "error" in r:
                    return JSONResponse(r, status_code=400)
                for _ in range(clicks):
                    r = _xdotool(["click", btn])
                    if "error" in r:
                        return JSONResponse(r, status_code=400)
                return JSONResponse({"ok": True, "action": action})
            else:
                return JSONResponse({"error": f"Unknown action: {action}"}, status_code=400)
        except KeyError as e:
            return JSONResponse({"error": f"Missing field: {e}"}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    # -- Health --

    @mcp.custom_route("/api/health", methods=["GET"])
    async def api_health(request: Request) -> Response:
        issues = []
        # Check desktop image exists (via Docker API, works through tcp proxy)
        image = manager.config.image
        try:
            docker_host = os.environ.get("DOCKER_HOST", "")
            if docker_host.startswith("tcp://"):
                base = docker_host.replace("tcp://", "http://")
            else:
                base = "http://localhost:2375"
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{base}/v1.43/images/{image}/json")
                if r.status_code != 200:
                    issues.append(f"Desktop image '{image}' not found. Run ./setup.sh")
        except Exception:
            pass
        return JSONResponse({
            "ok": len(issues) == 0,
            "desktops": len(manager._desktops),
            "issues": issues,
            "ts": time.time(),
        })

    # -- SSE Event Stream --

    @mcp.custom_route("/api/events", methods=["GET"])
    async def api_events(request: Request) -> Response:
        if not _check_auth(request):
            return _auth_error()

        async def event_stream():
            queue = event_bus.subscribe()
            try:
                # Send initial state
                desktops = []
                for did, info in manager._desktops.items():
                    desktops.append({
                        "id": did,
                        "state": info.state.value if hasattr(info.state, 'value') else str(info.state),
                        "label": info.label,
                    })
                init_msg = json.dumps({
                    "event": "init",
                    "desktops": desktops,
                    "ts": time.time(),
                })
                yield f"data: {init_msg}\n\n"

                # Stream events
                while True:
                    try:
                        msg = await asyncio.wait_for(queue.get(), timeout=30)
                        yield f"data: {json.dumps(msg)}\n\n"
                    except asyncio.TimeoutError:
                        # Send keepalive
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                event_bus.unsubscribe(queue)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # -- Agent Registry & Sessions --

    from .globals import registry

    def _check_admin(request: Request) -> bool:
        """Admin = API token auth (existing SCREENBOX_API_TOKEN)."""
        return _check_auth(request)

    @mcp.custom_route("/api/agent/register", methods=["POST"])
    async def api_agent_register(request: Request) -> Response:
        """Admin: register new agent. Returns api_key (shown once)."""
        if not _check_admin(request):
            return _auth_error()
        body = await request.json()
        agent_id = body.get("agent_id", "").strip()
        display_name = body.get("display_name", "").strip()
        if not agent_id:
            return JSONResponse({"error": "agent_id required"}, status_code=400)
        try:
            api_key = registry.register_agent(agent_id, display_name)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        return JSONResponse({
            "agent_id": agent_id,
            "api_key": api_key,
            "note": "Save this key -- it cannot be retrieved later",
        })

    @mcp.custom_route("/api/agent/login", methods=["POST"])
    async def api_agent_login(request: Request) -> Response:
        """Agent: authenticate with agent_id + api_key. Returns session token."""
        source_ip = request.client.host if request.client else ""
        if not _rate_check(source_ip):
            return JSONResponse({"error": "Too many attempts, try again later"}, status_code=429)
        body = await request.json()
        agent_id = body.get("agent_id", "").strip()
        api_key = body.get("api_key", "").strip()
        if not agent_id or not api_key:
            return JSONResponse({"error": "agent_id and api_key required"}, status_code=400)
        token = registry.login(agent_id, api_key, source_ip)
        if not token:
            return JSONResponse({"error": "Invalid credentials or agent suspended"}, status_code=401)
        # Return agent's assigned desktops
        desktops = registry.get_agent_desktops(agent_id)
        return JSONResponse({
            "session_token": token,
            "agent_id": agent_id,
            "desktops": desktops,
        })

    @mcp.custom_route("/api/agent/logout", methods=["POST"])
    async def api_agent_logout(request: Request) -> Response:
        """Agent: end session."""
        body = await request.json()
        token = body.get("session_token", "")
        registry.logout(token)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/agent/list", methods=["GET"])
    async def api_agent_list(request: Request) -> Response:
        """Admin: list all registered agents."""
        if not _check_admin(request):
            return _auth_error()
        return JSONResponse(registry.list_agents())

    @mcp.custom_route("/api/agent/suspend", methods=["POST"])
    async def api_agent_suspend(request: Request) -> Response:
        """Admin: suspend agent."""
        if not _check_admin(request):
            return _auth_error()
        body = await request.json()
        agent_id = body.get("agent_id", "")
        if registry.suspend_agent(agent_id):
            return JSONResponse({"ok": True, "agent_id": agent_id})
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    @mcp.custom_route("/api/agent/activate", methods=["POST"])
    async def api_agent_activate(request: Request) -> Response:
        """Admin: re-activate suspended agent."""
        if not _check_admin(request):
            return _auth_error()
        body = await request.json()
        agent_id = body.get("agent_id", "")
        if registry.activate_agent(agent_id):
            return JSONResponse({"ok": True, "agent_id": agent_id})
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    @mcp.custom_route("/api/agent/delete", methods=["POST"])
    async def api_agent_delete(request: Request) -> Response:
        """Admin: delete agent and all assignments."""
        if not _check_admin(request):
            return _auth_error()
        body = await request.json()
        agent_id = body.get("agent_id", "")
        if registry.delete_agent(agent_id):
            return JSONResponse({"ok": True, "agent_id": agent_id})
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    @mcp.custom_route("/api/agent/reset-key", methods=["POST"])
    async def api_agent_reset_key(request: Request) -> Response:
        """Admin: generate new API key for agent."""
        if not _check_admin(request):
            return _auth_error()
        body = await request.json()
        agent_id = body.get("agent_id", "")
        new_key = registry.reset_agent_key(agent_id)
        if new_key:
            return JSONResponse({
                "agent_id": agent_id,
                "api_key": new_key,
                "note": "Save this key -- it cannot be retrieved later",
            })
        return JSONResponse({"error": "Agent not found"}, status_code=404)

    # -- Sessions --

    @mcp.custom_route("/api/sessions", methods=["GET"])
    async def api_sessions(request: Request) -> Response:
        """Admin or dashboard: list active sessions."""
        if not _check_admin(request):
            return _auth_error()
        return JSONResponse(registry.list_sessions())

    # -- Desktop Assignments --

    @mcp.custom_route("/api/desktop/assign", methods=["POST"])
    async def api_desktop_assign(request: Request) -> Response:
        """Assign desktop to agent. Agent auth (session_token) or admin."""
        body = await request.json()
        desktop_id = body.get("desktop_id", "")
        # Agent self-assign via session token
        session_token = body.get("session_token", "")
        if session_token:
            session = registry.validate_session(session_token)
            if not session:
                return JSONResponse({"error": "Invalid session"}, status_code=401)
            agent_id = session["agent_id"]
        elif _check_admin(request):
            agent_id = body.get("agent_id", "")
        else:
            return _auth_error()

        if not desktop_id or not agent_id:
            return JSONResponse({"error": "desktop_id and agent_id required"}, status_code=400)

        if registry.assign_desktop(desktop_id, agent_id):
            # Also acquire in manager for runtime lock
            manager.acquire(desktop_id, agent_id)
            return JSONResponse({"ok": True, "desktop_id": desktop_id, "agent_id": agent_id})
        assigned_to = registry.get_assignment(desktop_id)
        return JSONResponse({
            "error": f"Desktop '{desktop_id}' already assigned to '{assigned_to}'",
        }, status_code=409)

    @mcp.custom_route("/api/desktop/unassign", methods=["POST"])
    async def api_desktop_unassign(request: Request) -> Response:
        """Admin-only: release desktop assignment."""
        if not _check_admin(request):
            return _auth_error()
        body = await request.json()
        desktop_id = body.get("desktop_id", "")
        if registry.release_desktop(desktop_id):
            manager.release(desktop_id)
            return JSONResponse({"ok": True, "desktop_id": desktop_id})
        return JSONResponse({"error": "Desktop not assigned"}, status_code=404)

    @mcp.custom_route("/api/desktop/assignments", methods=["GET"])
    async def api_desktop_assignments(request: Request) -> Response:
        """List all desktop assignments. Admin or dashboard."""
        if not _check_admin(request):
            return _auth_error()
        return JSONResponse(registry.list_assignments())

    @mcp.custom_route("/api/desktop/my", methods=["POST"])
    async def api_desktop_my(request: Request) -> Response:
        """Agent: list my assigned desktops."""
        body = await request.json()
        session_token = body.get("session_token", "")
        session = registry.validate_session(session_token)
        if not session:
            return JSONResponse({"error": "Invalid session"}, status_code=401)
        agent_id = session["agent_id"]
        desktop_ids = registry.get_agent_desktops(agent_id)
        desktops = []
        for did in desktop_ids:
            info = manager._desktops.get(did)
            desktops.append({
                "id": did,
                "state": info.state.value if info else "unknown",
                "assigned_at": None,
            })
        return JSONResponse({"agent_id": agent_id, "desktops": desktops})

    # -- Desktop Sharing --

    @mcp.custom_route("/api/desktop/share", methods=["POST"])
    async def api_desktop_share_create(request: Request) -> Response:
        """Create share link. Agent (session_token) or admin (Bearer)."""
        body = await request.json()
        desktop_id = body.get("desktop_id", "").strip()
        ttl = int(body.get("ttl", 3600))
        if ttl < 60 or ttl > 86400:
            return JSONResponse({"error": "ttl must be 60-86400 seconds"}, status_code=400)
        if not desktop_id:
            return JSONResponse({"error": "desktop_id required"}, status_code=400)
        if desktop_id not in manager._desktops:
            return JSONResponse({"error": "Desktop not found"}, status_code=404)

        # Auth: agent session or admin token
        session_token = body.get("session_token", "")
        created_by = ""
        if session_token:
            session = registry.validate_session(session_token)
            if not session:
                return JSONResponse({"error": "Invalid session"}, status_code=401)
            # Agent can only share their own desktops
            agent_desktops = registry.get_agent_desktops(session["agent_id"])
            if desktop_id not in agent_desktops:
                return JSONResponse({"error": "Desktop not assigned to you"}, status_code=403)
            created_by = session["agent_id"]
        elif not _check_admin(request):
            return _auth_error()

        share = registry.create_share(desktop_id, ttl=ttl, created_by=created_by)
        # Build URL: prefer explicit config, fallback to request headers
        import re
        base_url = os.environ.get("SCREENBOX_SHARE_BASE_URL", "")
        if not base_url:
            scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
            host = request.headers.get("host", "")
            # Sanitize: only allow hostname[:port], no paths or special chars
            if not re.match(r'^[a-zA-Z0-9._-]+(:\d+)?$', host):
                host = "localhost"
            base_url = f"{scheme}://{host}"
        share["url"] = f"{base_url}/s/{share['token']}"
        return JSONResponse(share)

    @mcp.custom_route("/api/desktop/share", methods=["DELETE"])
    async def api_desktop_share_revoke(request: Request) -> Response:
        """Revoke share link. Agent (own) or admin (any)."""
        body = await request.json()
        token = body.get("token", "").strip()

        if not token:
            return JSONResponse({"error": "token required"}, status_code=400)

        # Check who is revoking
        session_token = body.get("session_token", "")
        if session_token:
            session = registry.validate_session(session_token)
            if not session:
                return JSONResponse({"error": "Invalid session"}, status_code=401)
            # Agent can only revoke their own shares
            share_info = registry.validate_share(token)
            if not share_info or share_info["created_by"] != session["agent_id"]:
                return JSONResponse({"error": "Not your share link"}, status_code=403)
        elif not _check_admin(request):
            return _auth_error()

        if registry.revoke_share(token):
            return JSONResponse({"ok": True, "revoked": token})
        return JSONResponse({"error": "Share not found"}, status_code=404)

    @mcp.custom_route("/api/desktop/shares", methods=["GET"])
    async def api_desktop_shares_list(request: Request) -> Response:
        """Admin: list active share links."""
        if not _check_admin(request):
            return _auth_error()
        desktop_id = request.query_params.get("desktop_id", "")
        shares = registry.list_shares(desktop_id)
        return JSONResponse(shares)

    @mcp.custom_route("/api/desktop/shares", methods=["DELETE"])
    async def api_desktop_shares_revoke_all(request: Request) -> Response:
        """Admin: revoke all shares (optionally for a desktop)."""
        if not _check_admin(request):
            return _auth_error()
        body = await request.json()
        desktop_id = body.get("desktop_id", "").strip()
        if desktop_id:
            count = registry.revoke_desktop_shares(desktop_id)
        else:
            count = registry.revoke_all_shares()
        return JSONResponse({"ok": True, "revoked_count": count})

    @mcp.custom_route("/api/desktop/share/validate", methods=["GET"])
    async def api_desktop_share_validate(request: Request) -> Response:
        """Public: validate share token and get desktop info for viewer."""
        token = request.query_params.get("token", "")
        if not token:
            return JSONResponse({"error": "token required"}, status_code=400)
        share = registry.validate_share(token)
        if not share:
            return JSONResponse({"error": "Invalid or expired share link"}, status_code=404)
        desktop_id = share["desktop_id"]
        info = manager._desktops.get(desktop_id)
        if not info:
            return JSONResponse({"error": "Desktop not found"}, status_code=404)
        return JSONResponse({
            "desktop_id": desktop_id,
            "state": info.state.value if hasattr(info.state, 'value') else str(info.state),
            "label": info.label,
            "expires_at": share["expires_at"],
        })

    # -- Knowledge API (single source of truth for dashboard) --

    from .knowledge import get_store, _slugify

    def _desktop_param(request: Request) -> str | None:
        """Extract desktop_id from query params."""
        did = request.query_params.get("desktop_id", "")
        return did if did else None

    @mcp.custom_route("/api/knowledge", methods=["GET"])
    async def api_knowledge_list(request: Request) -> Response:
        """List all knowledge entries. ?desktop_id= for per-desktop scope."""
        if not _check_auth(request):
            return _auth_error()
        store = get_store()
        desktop_id = _desktop_param(request)
        entries = store.list_all(desktop_id)
        # Map to dashboard-expected format: app, facts_count
        for e in entries:
            e["app"] = e.get("name", e.get("slug", ""))
            e["facts_count"] = e.get("facts", 0)
        return JSONResponse(entries)

    @mcp.custom_route("/api/knowledge/desktops", methods=["GET"])
    async def api_knowledge_desktops(request: Request) -> Response:
        """List desktop IDs that have their own knowledge."""
        if not _check_auth(request):
            return _auth_error()
        store = get_store()
        return JSONResponse(store.list_desktops())

    @mcp.custom_route("/api/knowledge/importable", methods=["GET"])
    async def api_knowledge_importable(request: Request) -> Response:
        """List all knowledge files with their load status (for import UI)."""
        if not _check_auth(request):
            return _auth_error()
        store = get_store()
        desktop_id = _desktop_param(request)
        entries = store.list_all(desktop_id)
        for e in entries:
            e["loaded"] = True
        return JSONResponse(entries)

    @mcp.custom_route("/api/knowledge/import-server", methods=["POST"])
    async def api_knowledge_import_server(request: Request) -> Response:
        """Import a knowledge file by slug. Body: {slug, kind, mode}."""
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        slug = body.get("slug", "")
        kind = body.get("kind", "app")
        if not slug:
            return JSONResponse({"error": "slug required"}, status_code=400)
        desktop_id = body.get("desktop_id")
        store = get_store()
        data = store._load(slug, kind, desktop_id)
        facts = data.get("facts", [])
        if not facts:
            return JSONResponse({"error": "No facts found"}, status_code=404)
        app_name = data.get("name", slug)
        return JSONResponse({
            "ok": True, "imported": len(facts), "skipped": 0,
            "total": len(facts), "slug": slug, "app": app_name,
        })

    @mcp.custom_route("/api/knowledge/upload", methods=["POST"])
    async def api_knowledge_upload(request: Request) -> Response:
        """Upload/import a JSON knowledge file. Body: {slug, app, kind, mode, facts}."""
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        slug = body.get("slug", "")
        if not slug:
            return JSONResponse({"error": "slug required"}, status_code=400)
        slug = _slugify(slug)
        facts = body.get("facts", [])
        if not isinstance(facts, list):
            return JSONResponse({"error": "facts must be an array"}, status_code=400)
        kind = body.get("kind", "app")
        app_name = body.get("app", body.get("name", slug))
        mode = body.get("mode", "merge")
        desktop_id = body.get("desktop_id")
        store = get_store()
        if mode == "replace":
            from datetime import datetime as _dt, timezone as _tz
            data = {"name": app_name, "kind": kind, "tags": [], "references": [], "facts": []}
            for fact in facts:
                text = (fact.get("text") or "").strip()
                if not text:
                    continue
                triggers = fact.get("triggers", [])
                if isinstance(triggers, str):
                    triggers = [t.strip() for t in triggers.split(",") if t.strip()]
                if not triggers:
                    triggers = [w.lower() for w in text.split() if len(w) > 3][:10]
                data["facts"].append({
                    "text": text,
                    "triggers": [t.lower().strip() for t in triggers if t.strip()],
                    "added": fact.get("added", _dt.now(_tz.utc).isoformat()),
                    "source": fact.get("source", "upload"),
                })
            store._save(slug, data, desktop_id)
            return JSONResponse({"ok": True, "imported": len(data["facts"]), "skipped": 0,
                                 "total": len(data["facts"]), "slug": slug, "app": app_name})
        # Merge mode
        imported = 0
        skipped = 0
        for fact in facts:
            text = (fact.get("text") or "").strip()
            if not text:
                continue
            triggers = fact.get("triggers", [])
            if isinstance(triggers, str):
                triggers = [t.strip() for t in triggers.split(",") if t.strip()]
            if not triggers:
                triggers = [w.lower() for w in text.split() if len(w) > 3][:10]
            source = fact.get("source", "upload")
            result = store.add_fact(slug, text, triggers, kind=kind, name=app_name, source=source, desktop_id=desktop_id)
            if result.get("source") == source:
                imported += 1
            else:
                skipped += 1
        total = len(store._load(slug, kind, desktop_id).get("facts", []))
        return JSONResponse({"ok": True, "imported": imported, "skipped": skipped,
                             "total": total, "slug": slug, "app": app_name})

    @mcp.custom_route("/api/knowledge/{slug}", methods=["GET"])
    async def api_knowledge_get(request: Request) -> Response:
        """Get full knowledge for an app/flow/site slug."""
        if not _check_auth(request):
            return _auth_error()
        slug = request.path_params["slug"]
        kind = request.query_params.get("kind", "")
        desktop_id = _desktop_param(request)
        store = get_store()
        if kind:
            data = store._load(slug, kind, desktop_id)
        else:
            # Auto-detect kind: try app, flow, site -- return first with facts
            data = store._load(slug, "app", desktop_id)
            if not data.get("facts"):
                for try_kind in ("flow", "site", "os"):
                    candidate = store._load(slug, try_kind, desktop_id)
                    if candidate.get("facts"):
                        data = candidate
                        break
        return JSONResponse(data)

    @mcp.custom_route("/api/knowledge/{slug}/facts", methods=["POST"])
    async def api_knowledge_add_fact(request: Request) -> Response:
        """Add a fact to a knowledge entry."""
        if not _check_auth(request):
            return _auth_error()
        slug = request.path_params["slug"]
        body = await request.json()
        text = (body.get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "text required"}, status_code=400)
        triggers = body.get("triggers", [])
        if isinstance(triggers, str):
            triggers = [t.strip() for t in triggers.split(",") if t.strip()]
        if not triggers:
            triggers = [w.lower() for w in text.split() if len(w) > 3][:10]
        kind = body.get("kind", "app")
        app_name = body.get("app_name", body.get("app", slug))
        source = body.get("source", "dashboard")
        desktop_id = body.get("desktop_id")
        store = get_store()
        existing_facts = store._load(slug, kind, desktop_id).get("facts", [])
        text_lower = text.lower().strip()
        for f in existing_facts:
            if f.get("text", "").lower().strip() == text_lower:
                return JSONResponse({"ok": True, "duplicate": True, "total": len(existing_facts)})
        store.add_fact(slug, text, triggers, kind=kind, name=app_name, source=source, desktop_id=desktop_id)
        total = len(store._load(slug, kind, desktop_id).get("facts", []))
        return JSONResponse({"ok": True, "total": total})

    @mcp.custom_route("/api/knowledge/{slug}", methods=["DELETE"])
    async def api_knowledge_delete_app(request: Request) -> Response:
        """Delete entire knowledge entry."""
        if not _check_auth(request):
            return _auth_error()
        slug = request.path_params["slug"]
        kind = request.query_params.get("kind", "app")
        desktop_id = _desktop_param(request)
        store = get_store()
        path = store._path_for(slug, kind, desktop_id)
        if path.exists():
            path.unlink()
            store._cache.pop(f"{desktop_id or 'global'}:{kind}:{slug}", None)
            return JSONResponse({"ok": True, "slug": slug})
        return JSONResponse({"error": "not found"}, status_code=404)

    @mcp.custom_route("/api/knowledge/{slug}/facts/{index}", methods=["DELETE"])
    async def api_knowledge_delete_fact(request: Request) -> Response:
        """Delete a single fact by index."""
        if not _check_auth(request):
            return _auth_error()
        slug = request.path_params["slug"]
        kind = request.query_params.get("kind", "app")
        try:
            idx = int(request.path_params["index"])
        except ValueError:
            return JSONResponse({"error": "Invalid index"}, status_code=400)
        desktop_id = _desktop_param(request)
        store = get_store()
        data = store._load(slug, kind, desktop_id)
        facts = data.get("facts", [])
        if idx < 0 or idx >= len(facts):
            return JSONResponse({"error": "index out of range"}, status_code=404)
        removed = facts.pop(idx)
        store._save(slug, data, desktop_id)
        return JSONResponse({"ok": True, "removed": removed.get("text", "")[:100], "total": len(facts)})

    # -- Knowledge Compilation API --

    @mcp.custom_route("/api/knowledge/compile", methods=["POST"])
    async def api_knowledge_compile(request: Request) -> Response:
        """Compile session logs into candidate knowledge facts via LLM."""
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        desktop_id = body.get("desktop_id", "")
        if not desktop_id:
            return JSONResponse({"error": "desktop_id required"}, status_code=400)

        from .llm_client import is_configured
        if not is_configured():
            return JSONResponse({"error": "LLM not configured. Set SCREENBOX_LLM_KEY."}, status_code=500)

        session_id = body.get("session_id", "latest")
        task = body.get("task", "")

        import asyncio
        try:
            result = await asyncio.to_thread(_compile_knowledge_sync, desktop_id, session_id, task)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return JSONResponse(result)

    def _compile_knowledge_sync(desktop_id: str, session_id: str, task: str) -> dict:
        from .knowledge_compiler import compile_from_log, _read_session_log
        entries, actual_sid = _read_session_log(desktop_id, session_id)
        if not entries:
            return {"error": f"No log entries for {desktop_id}/{session_id}", "candidates": []}
        store = get_store()
        all_entries = store.list_all(desktop_id)
        existing = []
        for e in all_entries:
            data = store._load(e["slug"], e.get("kind", "app"), desktop_id)
            existing.extend(data.get("facts", []))
        candidates = compile_from_log(entries, task, existing)
        domains = {}
        for c in candidates:
            d = c.get("domain", "unknown")
            domains[d] = domains.get(d, 0) + 1
        suggested = max(domains, key=domains.get) if domains else "unknown"
        return {
            "candidates": candidates,
            "suggested_target": {"slug": suggested, "kind": "app"},
            "session_id": actual_sid,
            "log_entries_analyzed": len(entries),
        }

    @mcp.custom_route("/api/knowledge/merge", methods=["POST"])
    async def api_knowledge_merge(request: Request) -> Response:
        """Preview or apply merge of compiled facts into existing knowledge."""
        if not _check_auth(request):
            return _auth_error()
        body = await request.json()
        facts = body.get("facts", [])
        target = body.get("target", "")
        if not facts or not target:
            return JSONResponse({"error": "facts and target required"}, status_code=400)
        kind = body.get("kind", "app")
        mode = body.get("mode", "preview")
        desktop_id = body.get("desktop_id")

        from .knowledge_compiler import preview_merge, apply_merge
        diff = preview_merge(facts, target, kind, desktop_id)
        if mode == "apply":
            result = apply_merge(diff, target, kind, desktop_id)
            return JSONResponse({"mode": "apply", "applied": True, **result})
        return JSONResponse({"mode": "preview", "diff": diff})

    # -- Logs API (single source of truth for dashboard) --

    @mcp.custom_route("/api/logs/{desktop_id}/recent", methods=["GET"])
    async def api_logs_recent(request: Request) -> Response:
        """Get recent log entries for a desktop."""
        if not _check_auth(request):
            return _auth_error()
        desktop_id = request.path_params["desktop_id"]
        limit = min(int(request.query_params.get("limit", "100")), 500)
        after = request.query_params.get("after", "")

        from .config import Config
        config = Config()
        logs_dir = config.logs_dir

        def _read_logs():
            entries = []
            # Flat file
            flat = os.path.join(logs_dir, f"{desktop_id}.jsonl")
            if os.path.isfile(flat):
                entries.extend(_read_jsonl_tail(flat))
            else:
                # Per-desktop dir with session files
                ddir = os.path.join(logs_dir, desktop_id)
                if os.path.isdir(ddir):
                    session_files = sorted(
                        [f for f in os.listdir(ddir) if f.endswith(".jsonl")],
                        key=lambda f: os.path.getmtime(os.path.join(ddir, f)),
                    )
                    for sf in session_files[-3:]:
                        entries.extend(_read_jsonl_tail(os.path.join(ddir, sf), 256 * 1024))
            # Filter and sort
            if after:
                entries = [e for e in entries if e.get("timestamp", "") > after]
            entries.sort(key=lambda e: e.get("timestamp", ""))
            return entries[-limit:]

        try:
            data = await asyncio.to_thread(_read_logs)
            return JSONResponse(data)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    @mcp.custom_route("/api/logs/{desktop_id}/download", methods=["GET"])
    async def api_logs_download(request: Request) -> Response:
        """Download logs as JSONL attachment."""
        if not _check_auth(request):
            return _auth_error()
        desktop_id = request.path_params["desktop_id"]

        from .config import Config
        config = Config()
        logs_dir = config.logs_dir

        def _collect():
            entries = []
            flat = os.path.join(logs_dir, f"{desktop_id}.jsonl")
            if os.path.isfile(flat):
                entries.extend(_read_jsonl_tail(flat, 2 * 1024 * 1024))
            else:
                ddir = os.path.join(logs_dir, desktop_id)
                if os.path.isdir(ddir):
                    session_files = sorted(
                        [f for f in os.listdir(ddir) if f.endswith(".jsonl")],
                        key=lambda f: os.path.getmtime(os.path.join(ddir, f)),
                    )
                    for sf in session_files[-5:]:
                        entries.extend(_read_jsonl_tail(os.path.join(ddir, sf), 512 * 1024))
            entries.sort(key=lambda e: e.get("timestamp", ""))
            return entries[-10000:]

        try:
            entries = await asyncio.to_thread(_collect)
            content = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n"
            return Response(
                content=content.encode("utf-8"),
                media_type="application/x-ndjson",
                headers={"Content-Disposition": f'attachment; filename="{desktop_id}-logs.jsonl"'},
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    log.info("HTTP API registered: /api/desktop/*, /api/agent/*, /api/knowledge/*, /api/logs/*, /api/events, /api/health")
