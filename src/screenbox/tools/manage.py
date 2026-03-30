"""desktop_manage dispatcher tool -- lifecycle, snapshots, clipboard, apps, processes, input, wait, system."""
import hashlib
import importlib
import json
import logging
import os
import shlex
import subprocess
import time
from typing import Optional

from ..desktop import Desktop
from ..manager import DesktopState

log = logging.getLogger(__name__)

# Host for RDP URLs in API responses (localhost for stdio, configurable for SSE)
_RDP_HOST = os.environ.get("SCREENBOX_NOVNC_HOST", "localhost")


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    def _desktop_create(desktop_id: str, label=None, url="none", resolution=None, image=None,
                        profile_json=None, profile_name=None):
        """Create a new isolated virtual desktop with Chromium browser."""
        t0 = time.time()
        mgr = get_manager()
        info = mgr.create(desktop_id, label=label, url=url, resolution=resolution, image=image,
                          profile_json=profile_json, profile_name=profile_name)

        # Extension warmup: verify Chrome extension is connected (3 attempts, 1s apart)
        ext_ready = False
        desktop = None

        from ..globals import get_logger, AGENT_ID
        for attempt in range(3):
            try:
                desktop = Desktop(mgr, desktop_id, action_logger=get_logger(desktop_id), agent_id=AGENT_ID)
                desktop._ext_cmd("ping", timeout=2)
                ext_ready = True
                break
            except Exception:
                if desktop and desktop._ext_client:
                    desktop._ext_client.close()
                    desktop._ext_client = None
                time.sleep(1)

        # Auto-assign to authenticated agent (owner = creator)
        from ..globals import guard as _guard
        from ..request_context import get_current_agent
        owner = get_current_agent() or _guard.current_agent
        if owner and owner != "admin":
            _guard.auto_assign(desktop_id, owner)
            mgr.acquire(desktop_id, owner)

        result = {
            "desktop_id": info.desktop_id,
            "state": info.state.value,
            "rdp_port": info.rdp_port,
            "ws_port": info.ws_port,
            "label": info.label,
            "resolution": f"{info.screen_width}x{info.screen_height}",
            "extension_ready": ext_ready,
            "assigned_to": owner,
        }
        log_action(desktop_id, "desktop_create", {"url": url, "label": label, "resolution": resolution}, result, t0)
        return json.dumps(result, indent=2)

    def _desktop_status(desktop_id: str):
        """Get desktop status."""
        mgr = get_manager()
        info = mgr.get(desktop_id)
        if not info:
            return json.dumps({"error": f"Desktop '{desktop_id}' not found"})
        result = {
            "desktop_id": info.desktop_id,
            "state": info.state.value,
            "rdp_port": info.rdp_port if info.state == DesktopState.RUNNING else None,
            "label": info.label,
            "created_at": info.created_at,
            "last_tool_call": info.last_tool_call,
            "error": info.error,
        }
        if info.acquired_by:
            result["acquired_by"] = info.acquired_by
            result["acquired_at"] = info.acquired_at
        mem = mgr.get_memory_info(desktop_id)
        if mem:
            result["memory"] = mem
        return json.dumps(result, indent=2)

    def _desktop_heartbeat(desktop_id: str, agent_id: str):
        """Keep-alive for acquired desktop."""
        mgr = get_manager()
        ok = mgr.heartbeat(desktop_id, agent_id)
        return json.dumps({"ok": ok, "desktop_id": desktop_id})

    def _desktop_install(desktop_id: str, app: str):
        """Install an application in the desktop container."""
        t0 = time.time()
        d = get_desktop(desktop_id)
        mgr = get_manager()

        catalog_entry = app_catalog.get(app)

        if catalog_entry and catalog_entry.get("install_script"):
            # Custom install script from internal catalog (trusted source).
            # Validate it's a string and doesn't contain obvious user-controlled data.
            script = catalog_entry["install_script"]
            if not isinstance(script, str) or len(script) > 2000:
                return json.dumps({"error": f"Invalid install script for '{app}'"})
            cmd = script
        elif catalog_entry:
            packages = [shlex.quote(p) for p in catalog_entry["packages"]]
            cmd = "apt-get update -qq && apt-get install -y --no-install-recommends " + " ".join(packages)
        else:
            # Direct apt package name -- sanitize user input
            cmd = "apt-get update -qq && apt-get install -y --no-install-recommends " + shlex.quote(app)

        # Run as root inside container
        result = mgr.exec(
            desktop_id,
            ["bash", "-c", cmd],
            timeout=120,
            user="root",
        )
        stdout = (result.stdout or b"").decode("utf-8", errors="replace")
        stderr = (result.stderr or b"").decode("utf-8", errors="replace")
        ok = result.returncode == 0
        out = {
            "installed": ok,
            "app": app,
            "exit_code": result.returncode,
            "message": f"{'Installed' if ok else 'Failed to install'} {app}",
        }
        if not ok:
            out["stderr"] = stderr[-500:]
        else:
            # Record installed packages in volume for auto-restore on image rebuild
            # Store actual apt package names (not catalog app names) so entrypoint can reinstall
            if catalog_entry:
                pkgs = " ".join(catalog_entry.get("packages", [app]))
            else:
                pkgs = app
            mgr.exec(desktop_id, [
                "bash", "-c",
                f'mkdir -p /home/screenbox/.screenbox && '
                f'for p in {pkgs}; do '
                f'grep -qxF "$p" /home/screenbox/.screenbox/installed-packages.txt 2>/dev/null || '
                f'echo "$p" >> /home/screenbox/.screenbox/installed-packages.txt; done'
            ], timeout=5, user="root")
        log_action(desktop_id, "desktop_install", {"app": app}, out, t0)
        return json.dumps(out, indent=2)

    def _desktop_uninstall(desktop_id: str, app: str):
        """Uninstall an application from the desktop container."""
        t0 = time.time()
        _ = get_desktop(desktop_id)  # validate desktop exists
        mgr = get_manager()

        catalog_entry = app_catalog.get(app)
        if catalog_entry:
            packages = " ".join(shlex.quote(p) for p in catalog_entry["packages"])
        else:
            packages = shlex.quote(app)

        result = mgr.exec(
            desktop_id,
            ["bash", "-c", f"apt-get remove -y {packages} && apt-get autoremove -y"],
            timeout=60,
            user="root",
        )
        ok = result.returncode == 0
        out = {
            "uninstalled": ok,
            "app": app,
            "exit_code": result.returncode,
        }
        log_action(desktop_id, "desktop_uninstall", {"app": app}, out, t0)
        return json.dumps(out, indent=2)

    def _screenbox_mcp_reload():
        """Hot-reload screenbox Python modules and rediscover desktops."""
        from screenbox import config as _cfg, desktop as _desk, logger as _log, manager as _mgr
        for mod in [_cfg, _log, _mgr, _desk]:
            importlib.reload(mod)
        mgr = get_manager()
        # Rediscover all containers (picks up externally-created desktops)
        before = set(mgr._desktops.keys())
        mgr._recover_existing()
        after = set(mgr._desktops.keys())
        # Refresh states for already-known desktops
        for did, info in mgr._desktops.items():
            try:
                chk = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Status}}:{{.State.Paused}}",
                     f"screenbox-{did}"],
                    capture_output=True, text=True, timeout=5,
                )
                line = chk.stdout.strip()
                if "running:false" in line:
                    info.state = DesktopState.RUNNING
                elif "running:true" in line:
                    info.state = DesktopState.PAUSED
            except Exception:
                pass
        new_desktops = after - before
        return json.dumps({
            "reloaded": True,
            "modules": ["config", "logger", "manager", "desktop"],
            "desktops_known": sorted(after),
            "desktops_discovered": sorted(new_desktops),
        })

    @mcp.tool()
    def desktop_manage(action: str, desktop_id: Optional[str] = None,
                       label: Optional[str] = None, url: Optional[str] = None,
                       resolution: Optional[str] = None,
                       image: Optional[str] = None,
                       confirm: bool = False,
                       save_snapshot: bool = True,
                       agent_id: Optional[str] = None,
                       snapshot_name: Optional[str] = None,
                       text: Optional[str] = None,
                       cell_size: int = 200,
                       app: Optional[str] = None, app_args: Optional[str] = None,
                       wait_ms: int = 2000,
                       pid: int = 0, proc_name: Optional[str] = None,
                       signal: int = 15, sort: str = "mem",
                       direction: str = "down", amount: int = 3,
                       x: int = 0, y: int = 0,
                       x2: int = 0, y2: int = 0,
                       button: int = 1,
                       timeout_ms: int = 10000,
                       title: Optional[str] = None,
                       threshold_ms: int = 500,
                       profile: Optional[str] = None,
                       intent: Optional[str] = None,
                       step: Optional[str] = None) -> str:
        """Desktop lifecycle, snapshots, and misc operations.

        Auth (register once, login per session):
          register(agent_id, label?) -- register new agent, returns api_key (save it!)
          login(agent_id, text=api_key) -- authenticate, returns session_token + assigned desktops
          logout -- end current session
          whoami -- show current auth status and assigned desktops

        Lifecycle:
          create(desktop_id, label?, url?, resolution?, image?, profile?) | destroy(desktop_id, confirm, save_snapshot?)
          list | status(desktop_id) | pause(desktop_id) | resume(desktop_id)
          acquire(desktop_id?, agent_id) | smart_acquire(agent_id, desktop_id?)
          release(desktop_id, agent_id?) | heartbeat(desktop_id, agent_id) | health(desktop_id)

        Snapshots:
          snapshot_save(desktop_id, label?) | snapshot_restore(desktop_id, snapshot_name?)
          snapshot_list(desktop_id)

        Clipboard:
          clipboard_get(desktop_id) | clipboard_set(desktop_id, text)

        Grid overlay:
          grid_on(desktop_id, cell_size?) | grid_off(desktop_id)

        Click indicator overlay:
          overlay(desktop_id, text="enabled=1,cursor=1,dots=1,trail=0")

        Apps:
          install(desktop_id, app) | uninstall(desktop_id, app)
          app_launch(desktop_id, app, app_args?, wait_ms?)

        Processes:
          proc_list(desktop_id, sort?) | proc_kill(desktop_id, pid?, proc_name?, signal?)

        Input (secondary):
          scroll(desktop_id, direction?, amount?)
          drag(desktop_id, x, y, x2, y2, button?)
          mouse_move(desktop_id, x, y)
          mouse_down(desktop_id, x, y, button?) | mouse_up(desktop_id, x, y, button?)
          right_click(desktop_id, x, y)

        Wait:
          wait_window(desktop_id, title, timeout_ms?)
          wait_idle(desktop_id, timeout_ms?, threshold_ms?)

        Sharing:
          share(desktop_id, text=ttl_seconds?) -- create view-only share link (default 1h)
          unshare(desktop_id) -- revoke all share links for this desktop

        System:
          reload

        Args:
            desktop_id: Desktop ID (required for most actions)
            action: Action to perform
            label: For create/snapshot_save
            url: Initial URL for create
            resolution: "WIDTHxHEIGHT" for create
            image: Docker image for create (e.g. "screenbox:mate", "screenbox:xfce"). Default: config
            confirm: Required for destroy -- must be True. Protects against accidental deletion.
            save_snapshot: For destroy (default True)
            agent_id: For acquire/release/heartbeat
            snapshot_name: For snapshot_restore
            text: For clipboard_set
            cell_size: For grid_on (default 200)
            app: For install/uninstall/app_launch
            app_args: Extra args for app_launch
            wait_ms: Window wait for app_launch (default 2000)
            pid: For proc_kill
            proc_name: For proc_kill (alternative to pid)
            signal: For proc_kill (default 15=TERM)
            sort: For proc_list (mem/cpu/pid)
            direction: For scroll (up/down/left/right)
            amount: For scroll (default 3)
            x: For drag/mouse_move/mouse_down/mouse_up/right_click
            y: For drag/mouse_move/mouse_down/mouse_up/right_click
            x2: End X for drag
            y2: End Y for drag
            button: For drag/mouse_down/mouse_up (default 1)
            timeout_ms: For wait_window/wait_idle
            title: Window title for wait_window
            threshold_ms: Stability threshold for wait_idle (default 500)
        """
        t0 = time.time()
        mgr = get_manager()
        # Normalize None -> "" for string params (MCP clients send null)
        desktop_id = desktop_id or ""
        label = label or ""
        url = url or ""
        agent_id = agent_id or ""
        snapshot_name = snapshot_name or ""
        text = text or ""
        app = app or ""
        app_args = app_args or ""
        proc_name = proc_name or ""
        title = title or ""
        intent = intent or ""
        step = step or ""

        # --- Agent Auth (unified with HTTP API) ---
        if action == "register":
            # Register new agent (admin-only via MCP)
            from ..globals import registry as _reg
            if not agent_id:
                return json.dumps({"error": "register requires agent_id"})
            try:
                api_key = _reg.register_agent(agent_id, label or agent_id)
            except ValueError as e:
                return json.dumps({"error": str(e)})
            result = {
                "registered": True,
                "agent_id": agent_id,
                "api_key": api_key,
                "note": "Save this key -- it cannot be retrieved later",
            }
        elif action == "login":
            # Login: agent_id + api_key (in text param) -> session
            from ..globals import guard as _guard, registry as _reg
            if not agent_id or not text:
                return json.dumps({"error": "login requires agent_id and text=api_key"})
            token = _guard.authenticate(agent_id, text)
            if not token:
                return json.dumps({"error": "Invalid credentials or agent suspended"})
            # Set MCP connection-level session
            _guard.set_session(agent_id, token)
            desktops = _reg.get_agent_desktops(agent_id)
            result = {
                "logged_in": True,
                "agent_id": agent_id,
                "session_token": token,
                "desktops": desktops,
            }
        elif action == "logout":
            from ..globals import guard as _guard
            if _guard.current_agent:
                _guard.clear_session()
            result = {"logged_out": True}
        elif action == "whoami":
            from ..globals import guard as _guard, registry as _reg
            from ..request_context import get_current_agent
            agent = get_current_agent() or _guard.current_agent
            if agent:
                desktops = _reg.get_agent_desktops(agent)
                result = {"agent_id": agent, "authenticated": True, "desktops": desktops}
            else:
                result = {"agent_id": None, "authenticated": False}

        # --- Lifecycle ---
        elif action == "create":
            from ..globals import guard as _guard
            try:
                _guard.check_desktop_create(_guard.current_agent)
            except ValueError as e:
                return json.dumps({"error": str(e)})
            if not desktop_id:
                return json.dumps({"error": "create requires desktop_id"})
            # Load profile if specified
            profile_data = None
            if profile:
                import pathlib
                profile_path = pathlib.Path(f"/data/screenbox/profiles/{profile}.json")
                if not profile_path.exists():
                    return json.dumps({"error": f"Profile '{profile}' not found. Available: " +
                        ", ".join(p.stem for p in pathlib.Path("/data/screenbox/profiles").glob("*.json"))})
                profile_data = json.loads(profile_path.read_text())
                # Apply container-level settings from profile
                if not resolution and "container" in profile_data:
                    c = profile_data["container"]
                    if c.get("screen_width") and c.get("screen_height"):
                        resolution = f"{c['screen_width']}x{c['screen_height']}"

            rv = _desktop_create(
                desktop_id, label=label or None,
                url=url or "none", resolution=resolution or None,
                image=image or None,
                profile_json=json.dumps(profile_data, indent=2) if profile_data else None,
                profile_name=profile if profile_data else None)

            return rv
        elif action == "destroy":
            # Auth required for destructive operations
            from ..request_context import get_current_agent, get_current_role
            from ..globals import guard as _guard
            agent = get_current_agent() or _guard.current_agent
            if not agent or agent == "unknown":
                return json.dumps({
                    "error": "Authentication required for destroy. "
                    "Pass API key via Authorization header."
                })
            # Check desktop lock
            from ..globals import registry as _reg
            if _reg.is_locked(desktop_id):
                lock = _reg.get_lock(desktop_id)
                return json.dumps({
                    "error": f"Desktop '{desktop_id}' is locked. "
                    f"Reason: {lock.get('reason', 'protected')}. "
                    "Use desktop_manage(action='unlock') to unlock first."
                })
            if not confirm:
                return json.dumps({
                    "error": "Destroy requires confirm=true. "
                    "This will remove the container (data in named volume is preserved). "
                    "Please confirm with the user before proceeding."
                })
            ok = mgr.destroy(desktop_id, auto_snapshot=save_snapshot)
            # Check if snapshot actually saved (not just requested)
            snap_dir = mgr.config.snapshot_dir(desktop_id)
            snap_exists = any(snap_dir.glob("*auto-before-destroy*")) if snap_dir.exists() else False
            result = {"destroyed": ok, "desktop_id": desktop_id,
                      "snapshot_saved": snap_exists if save_snapshot else False}
        elif action == "list":
            # Auto-discover externally-created containers (dashboard, docker CLI)
            mgr._recover_existing()
            desktops = mgr.list_desktops()
            for dd in desktops:
                if dd.get("state") == "running":
                    mem = mgr.get_memory_info(dd["desktop_id"])
                    if mem:
                        dd["memory"] = mem
            return json.dumps(desktops, indent=2)
        elif action == "status":
            # Auto-discover if desktop not known yet
            if desktop_id and not mgr.get(desktop_id):
                mgr._recover_existing()
            return _desktop_status(desktop_id)
        elif action == "pause":
            from ..request_context import get_current_agent
            from ..globals import guard as _guard
            agent = get_current_agent() or _guard.current_agent
            if not agent or agent == "unknown":
                return json.dumps({"error": "Authentication required for pause."})
            ok = mgr.pause(desktop_id)
            result = {"paused": ok, "desktop_id": desktop_id}
        elif action == "resume":
            from ..request_context import get_current_agent
            from ..globals import guard as _guard
            agent = get_current_agent() or _guard.current_agent
            if not agent or agent == "unknown":
                return json.dumps({"error": "Authentication required for resume."})
            ok = mgr.resume(desktop_id)
            result = {"resumed": ok, "desktop_id": desktop_id}
        elif action == "acquire":
            if not agent_id:
                return json.dumps({"error": "acquire requires agent_id"})
            if desktop_id:
                result = mgr.acquire(desktop_id, agent_id)
            else:
                # Smart acquire: auto-pick best available desktop
                result = mgr.smart_acquire(agent_id, desktop_id=None)
        elif action == "smart_acquire":
            if not agent_id:
                return json.dumps({"error": "smart_acquire requires agent_id"})
            result = mgr.smart_acquire(agent_id, desktop_id=desktop_id or None)
        elif action == "release":
            ok = mgr.release(desktop_id, agent_id or None)
            result = {"released": ok, "desktop_id": desktop_id}
        elif action == "heartbeat":
            return _desktop_heartbeat(desktop_id, agent_id)
        elif action == "health":
            d = get_desktop(desktop_id)
            result = d.health_check()

        # --- Snapshots ---
        elif action == "snapshot_save":
            filename = mgr.snapshot(desktop_id, label)
            result = {"saved": bool(filename), "filename": filename or "",
                      "desktop_id": desktop_id}
        elif action == "snapshot_restore":
            ok = mgr.restore(desktop_id, snapshot_name or None)
            result = {"restored": ok, "desktop_id": desktop_id}
        elif action == "snapshot_list":
            snaps = mgr.list_snapshots(desktop_id)
            return json.dumps(snaps, indent=2)

        # --- Clipboard ---
        elif action == "clipboard_get":
            d = get_desktop(desktop_id)
            content = d.get_clipboard()
            result = {"clipboard": content}
        elif action == "clipboard_set":
            d = get_desktop(desktop_id)
            d.set_clipboard(text)
            result = {"set": True, "length": len(text)}

        # --- Grid ---
        elif action == "grid_on":
            d = get_desktop(desktop_id)
            d.grid_on(cell_size=cell_size)
            result = {"grid": "on", "cell_size": cell_size}
        elif action == "grid_off":
            d = get_desktop(desktop_id)
            d.grid_off()
            result = {"grid": "off"}

        # --- Recording ---
        elif action == "record_start":
            d = get_desktop(desktop_id)
            result = d.record_start()
        elif action == "record_stop":
            d = get_desktop(desktop_id)
            result = d.record_stop()
        elif action == "record_status":
            d = get_desktop(desktop_id)
            result = d.record_status()
        elif action == "record_list":
            # List recordings from shared volume or host storage
            rec_dir = os.path.join("/data/screenbox/desktops", desktop_id, "recordings")
            if not os.path.isdir(rec_dir):
                rec_dir = os.path.expanduser(f"~/.screenbox/recordings/{desktop_id}")
            if not os.path.isdir(rec_dir):
                result = {"recordings": []}
            else:
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
                result = {"recordings": recs}

        # --- Apps & Packages ---
        elif action == "install":
            return _desktop_install(desktop_id, app)
        elif action == "uninstall":
            return _desktop_uninstall(desktop_id, app)
        elif action == "app_launch":
            d = get_desktop(desktop_id)
            # Chrome/browser: use start-chrome.sh (includes extension)
            app_lower = (app or "").lower()
            if app_lower in ("chrome", "chromium", "browser"):
                url = app_args or "about:blank"
                launch_result = d.shell(
                    f"/opt/screenbox/bin/start-chrome.sh {shlex.quote(url)}")
                pid = (launch_result.get("stdout") or "").strip()
                # Wait for extension to connect
                deadline = time.time() + wait_ms / 1000
                ext_ready = False
                while time.time() < deadline:
                    time.sleep(0.5)
                    try:
                        d._get_ext().send_command("ping", timeout=2)
                        ext_ready = True
                        break
                    except Exception:
                        if d._ext_client:
                            d._ext_client.close()
                            d._ext_client = None
                # Verify Chrome is running
                check = d.shell("pgrep -x chromium || pgrep -x chrome")
                running = bool((check.get("stdout") or "").strip())
                if not running:
                    stderr = d.shell("tail -5 /var/log/screenbox/chrome.log").get("stdout", "")
                    result = {"launched": False, "command": "start-chrome.sh",
                              "error": f"Chrome failed to start. Log: {stderr.strip()}"}
                else:
                    result = {"launched": True, "command": "start-chrome.sh",
                              "window_appeared": True, "extension_ready": ext_ready,
                              "chrome_pid": pid}
            else:
                # Non-browser apps
                aliases = {
                    "terminal": "mate-terminal", "term": "mate-terminal",
                    "editor": "pluma", "text-editor": "pluma",
                    "files": "caja", "file-manager": "caja",
                    "calc": "mate-calc", "calculator": "mate-calc",
                }
                cmd = aliases.get(app_lower)
                if cmd is None:
                    cmd = shlex.quote(app)
                if app_args:
                    try:
                        parts = shlex.split(app_args)
                        cmd = cmd + " " + " ".join(shlex.quote(p) for p in parts)
                    except ValueError:
                        cmd = cmd + " " + shlex.quote(app_args)
                # Check binary exists before launching
                which = d.shell(f"which {cmd.split()[0]} 2>/dev/null")
                if not (which.get("stdout") or "").strip():
                    result = {"launched": False, "command": cmd,
                              "error": f"Binary '{cmd.split()[0]}' not found in container"}
                else:
                    before = d.shell(
                        "DISPLAY=:99 xdotool search --onlyvisible --name '' 2>/dev/null | wc -l")
                    before_count = int(before.get("stdout", "0").strip() or "0")
                    d.shell(f"DISPLAY=:99 {cmd} &>/dev/null &")
                    deadline = time.time() + wait_ms / 1000
                    found = False
                    while time.time() < deadline:
                        time.sleep(0.3)
                        after = d.shell(
                            "DISPLAY=:99 xdotool search --onlyvisible --name '' 2>/dev/null | wc -l")
                        after_count = int(after.get("stdout", "0").strip() or "0")
                        if after_count > before_count:
                            found = True
                            break
                    result = {"launched": True, "command": cmd,
                              "window_appeared": found}

        # --- Processes ---
        elif action == "proc_list":
            d = get_desktop(desktop_id)
            sort_map = {"mem": "-%mem", "cpu": "-%cpu", "pid": "pid"}
            sort_key = sort_map.get(sort, "-%mem")
            r = d.shell(
                f"ps -eo pid,ppid,user,%cpu,%mem,vsz,rss,comm "
                f"--sort={sort_key} | head -30")
            result = {"processes": r.get("stdout", ""),
                      "exit_code": r.get("exit_code", 1)}
        elif action == "proc_kill":
            if not pid and not proc_name:
                return json.dumps({"error": "Provide pid or proc_name"})
            d = get_desktop(desktop_id)
            # Validate signal is numeric to prevent injection
            signal = int(signal)
            if pid:
                pid = int(pid)  # ensure numeric
                r = d.shell(f"kill -{signal} {pid} 2>&1; echo EXIT:$?")
            else:
                r = d.shell(
                    f"pkill -{signal} {shlex.quote(str(proc_name))} 2>&1; "
                    f"echo EXIT:$?")
            stdout = r.get("stdout", "").strip()
            result = {"killed": "EXIT:0" in stdout, "pid": pid,
                      "name": proc_name, "signal": signal}

        # --- Input (secondary) ---
        elif action == "scroll":
            _valid_dirs = {"up", "down", "left", "right"}
            if direction not in _valid_dirs:
                return json.dumps({"error": f"Invalid direction: {direction}. Must be one of: {', '.join(sorted(_valid_dirs))}"})
            d = get_desktop(desktop_id)
            d.scroll(direction, amount)
            result = {"scrolled": True, "direction": direction,
                      "amount": amount}
        elif action == "drag":
            d = get_desktop(desktop_id)
            d.drag(x, y, x2, y2, button)
            result = {"dragged": True, "from": [x, y], "to": [x2, y2]}
        elif action == "mouse_move":
            d = get_desktop(desktop_id)
            d.mouse_move(x, y)
            result = {"moved": True, "x": x, "y": y}
        elif action == "mouse_down":
            d = get_desktop(desktop_id)
            d.mouse_down(x, y, button)
            result = {"pressed": True, "x": x, "y": y, "button": button}
        elif action == "mouse_up":
            d = get_desktop(desktop_id)
            d.mouse_up(x, y, button)
            result = {"released": True, "x": x, "y": y, "button": button}
        elif action == "right_click":
            d = get_desktop(desktop_id)
            d.right_click(x, y)
            result = {"right_clicked": True, "x": x, "y": y}

        # --- Wait ---
        elif action == "wait_window":
            d = get_desktop(desktop_id)
            deadline = time.time() + timeout_ms / 1000
            while time.time() < deadline:
                r = d.shell(
                    f"xdotool search --name {shlex.quote(str(title))} "
                    f"2>/dev/null | head -1")
                wid = r.get("stdout", "").strip()
                if wid:
                    elapsed = int(
                        (timeout_ms / 1000 - (deadline - time.time())) * 1000)
                    result = {"found": True, "title": title,
                              "window_id": wid, "elapsed_ms": elapsed}
                    log_action(desktop_id, "desktop_manage",
                               {"action": action}, result, t0,
                               intent=intent, step=step)
                    return json.dumps(result)
                time.sleep(0.5)
            result = {"found": False, "title": title,
                      "timeout_ms": timeout_ms}
        elif action == "wait_idle":
            d = get_desktop(desktop_id)
            deadline = time.time() + timeout_ms / 1000
            last_hash = None
            stable_since = None
            while time.time() < deadline:
                img = d.screenshot()
                h = hashlib.md5(img).hexdigest()
                if h == last_hash:
                    if stable_since is None:
                        stable_since = time.time()
                    elif (time.time() - stable_since) * 1000 >= threshold_ms:
                        elapsed = int(
                            (timeout_ms / 1000 -
                             (deadline - time.time())) * 1000)
                        result = {"idle": True, "elapsed_ms": elapsed}
                        log_action(desktop_id, "desktop_manage",
                                   {"action": action}, result, t0,
                                   intent=intent, step=step)
                        return json.dumps(result)
                else:
                    last_hash = h
                    stable_since = None
                time.sleep(0.2)
            result = {"idle": False, "timeout_ms": timeout_ms}

        # --- Overlay ---
        elif action == "overlay":
            d = get_desktop(desktop_id)
            # Parse bool params from manage's signature (they default to generic values)
            # Use explicit keyword detection: any param not passed stays at default
            enabled = True  # default
            cursor = True
            dots = True
            trail = False  # trail off by default
            # Check if caller passed specific text values
            if text:
                # Parse "enabled=0,cursor=1,dots=1,trail=0" style
                for part in text.split(","):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if k == "enabled":
                            enabled = v == "1" or v.lower() == "true"
                        elif k == "cursor":
                            cursor = v == "1" or v.lower() == "true"
                        elif k == "dots":
                            dots = v == "1" or v.lower() == "true"
                        elif k == "trail":
                            trail = v == "1" or v.lower() == "true"
            result = d.overlay_mode(enabled=enabled, cursor=cursor,
                                    dots=dots, trail=trail)

        # --- Sharing ---
        elif action == "share":
            from ..globals import registry as _reg
            if not desktop_id:
                return json.dumps({"error": "share requires desktop_id"})
            ttl = int(text) if text and text.isdigit() else 3600
            if ttl < 60 or ttl > 86400:
                ttl = 3600
            share = _reg.create_share(desktop_id, ttl=ttl, created_by=agent_id)
            # Build URL using SCREENBOX_NOVNC_HOST or fallback
            host = os.environ.get("SCREENBOX_SHARE_HOST", "")
            if not host:
                host = os.environ.get("SCREENBOX_NOVNC_HOST", "localhost")
            scheme = "https" if host != "localhost" else "http"
            port = os.environ.get("SCREENBOX_DASHBOARD_PORT", "16000")
            if (scheme == "https" and port == "443") or (scheme == "http" and port == "80"):
                url = f"{scheme}://{host}/s/{share['token']}"
            elif host == "localhost":
                url = f"{scheme}://{host}:{port}/s/{share['token']}"
            else:
                url = f"{scheme}://{host}/s/{share['token']}"
            share["url"] = url
            result = share
        elif action == "unshare":
            from ..globals import registry as _reg
            if not desktop_id:
                return json.dumps({"error": "unshare requires desktop_id"})
            count = _reg.revoke_desktop_shares(desktop_id)
            result = {"revoked": count, "desktop_id": desktop_id}

        # --- System ---
        elif action == "reload":
            return _screenbox_mcp_reload()

        elif action == "lock":
            from ..request_context import get_current_agent, is_admin
            from ..globals import registry as _reg
            agent = get_current_agent()
            if not agent:
                return json.dumps({"error": "Authentication required for lock."})
            reason = text or label or "protected"
            _reg.lock_desktop(desktop_id, locked_by=agent or "admin", reason=reason)
            result = {"locked": True, "desktop_id": desktop_id, "reason": reason}

        elif action == "unlock":
            from ..request_context import get_current_agent, is_admin
            from ..globals import registry as _reg
            agent = get_current_agent()
            if not agent or not is_admin():
                return json.dumps({"error": "Admin authentication required for unlock."})
            ok = _reg.unlock_desktop(desktop_id)
            result = {"unlocked": ok, "desktop_id": desktop_id}

        else:
            return json.dumps({"error": f"Unknown manage action: {action}",
                               "available": [
                                   "create", "destroy", "list", "status",
                                   "pause", "resume", "acquire", "smart_acquire",
                                   "release", "heartbeat", "health",
                                   "snapshot_save", "snapshot_restore",
                                   "snapshot_list",
                                   "clipboard_get", "clipboard_set",
                                   "grid_on", "grid_off",
                                   "record_start", "record_stop",
                                   "record_status", "record_list",
                                   "install", "uninstall", "app_launch",
                                   "proc_list", "proc_kill",
                                   "scroll", "drag", "mouse_move",
                                   "mouse_down", "mouse_up", "right_click",
                                   "wait_window", "wait_idle",
                                   "overlay", "share", "unshare",
                                   "register", "login", "logout", "whoami",
                                   "reload"]})

        log_action(desktop_id or "system", "desktop_manage",
                   {"action": action}, result, t0,
                   intent=intent, step=step)
        return json.dumps(result, indent=2)
