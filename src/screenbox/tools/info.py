"""screenbox_info and screenbox_logs tools."""
import json
import os


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def screenbox_info() -> str:
        """Screenbox system info: architecture, config, desktops, available tools.

        Call this first when connecting to understand the system layout.
        Returns everything an agent needs to start working.
        """
        mgr = get_manager()
        cfg = mgr.config

        # Running desktops
        desktops = mgr.list_desktops()

        # Grid state per desktop
        for d in desktops:
            did = d.get("desktop_id", "")
            grid = mgr._grid_state.get(did)
            if grid:
                d["grid"] = f"{grid[0]}x{grid[1]}"

        # Config (safe subset)
        config_info = {
            "image": cfg.image,
            "default_viewport": cfg.get("default_viewport", "1920x1080"),
            "max_desktops": cfg.max_desktops,
            "memory_per_desktop": cfg.memory_limit,
            "idle_pause_minutes": cfg.get("idle_pause_minutes", 20),
            "look_factor": cfg.look_factor,
            "logs_dir": str(cfg.logs_dir),
        }

        # Architecture
        architecture = {
            "mcp_server": {
                "role": "MCP tool provider -- exposes desktop tools via Streamable HTTP/stdio",
                "transport": os.environ.get("SCREENBOX_TRANSPORT", "stdio"),
                "host": os.environ.get("SCREENBOX_HOST", "localhost"),
                "port": int(os.environ.get("SCREENBOX_PORT", "8080")),
            },
            "desktops": {
                "role": "Isolated Linux desktops in Docker containers",
                "display": "Xvnc (TigerVNC) on :99, RDP via xrdp on :3389",
                "desktop_env": "XFCE (light image) or MATE (full image)",
                "container_prefix": "screenbox-",
                "note": "Each desktop is a separate Docker container with its own display",
            },
            "connection": {
                "mcp_to_desktop": "Docker exec (xdotool, xclip, screenshot via import)",
                "agent_to_mcp": "MCP protocol over Streamable HTTP or stdio",
                "human_to_desktop": "RDP client on port 3389 (auto-assigned per desktop)",
            },
        }

        # Tool summary
        tools = {
            "core": [
                "desktop_screenshot -- see the screen (normalized 1000x1000 + grid overlay)",
                "desktop_look -- OCR a grid cell, get REAL click coordinates",
                "desktop_click -- click at coordinates (returns observe image by default)",
                "desktop_type -- type text character by character",
                "desktop_key -- press key combo (ctrl+s, Tab, Enter, Alt+F4...)",
                "desktop_batch -- sequence of type/key/click/wait actions",
                "desktop_shell -- run shell command inside desktop container",
                "desktop_help -- interaction strategy guide",
            ],
            "dispatchers": [
                "desktop_manage(action=create/destroy/list/status/pause/resume/install/...)",
                "desktop_window(action=list/activate/minimize/maximize/close/resize/move)",
                "desktop_chrome(action=page_map/eval/click/navigate/...)",
                "desktop_file(action=upload/download/list/upload_tar)",
            ],
            "debug": [
                "desktop_debug(action=on_screen/a11y_tree/a11y_find/hover/...) -- advanced/debug only",
            ],
            "info": [
                "screenbox_info -- this tool (system overview)",
                "screenbox_logs -- read recent action logs for a desktop",
                "desktop_help -- interaction patterns and anti-patterns",
            ],
        }

        # Workflow reminder
        workflow = {
            "mandatory": "screenshot -> look -> click (NEVER guess coordinates from screenshot)",
            "grid": "Set grid once with desktop_screenshot(grid='10x6'), it persists across calls",
            "observe": "desktop_click returns image+OCR by default -- no extra screenshot needed",
            "priority": "Window API > Keyboard > Look+Click > Chrome semantics",
        }

        return json.dumps({
            "architecture": architecture,
            "config": config_info,
            "desktops": desktops,
            "tools": tools,
            "workflow": workflow,
            "installable_apps": list(app_catalog.keys()),
        }, indent=2)

    @mcp.tool()
    def screenbox_logs(desktop_id: str, limit: int = 30,
                       tool_filter: str = "") -> str:
        """Read recent action logs for a desktop.

        Returns the last N tool calls with timestamps, args, duration, and results.
        Use this to debug issues, review what happened, or understand session history.

        Args:
            desktop_id: Desktop to get logs for
            limit: Number of recent entries (default 30, max 200)
            tool_filter: Only show entries matching this tool name (e.g. "desktop_click")
        """
        mgr = get_manager()
        cfg = mgr.config
        log_file = cfg.logs_dir / f"{desktop_id}.jsonl"

        if not log_file.exists():
            return json.dumps({"error": f"No logs found for '{desktop_id}'",
                               "log_path": str(log_file)})

        limit = min(limit, 200)

        # Read last N lines efficiently
        lines = []
        with open(log_file, "rb") as f:
            # Seek to end, read backwards to find last N lines
            f.seek(0, 2)
            file_size = f.tell()
            # Read up to 512KB from end (enough for ~200 entries)
            read_size = min(file_size, 512 * 1024)
            f.seek(file_size - read_size)
            chunk = f.read().decode("utf-8", errors="replace")
            all_lines = chunk.strip().split("\n")
            # Take last N+1 (first might be partial)
            candidates = all_lines[-(limit + 1):]
            for line in candidates:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if tool_filter and entry.get("tool", "") != tool_filter:
                        continue
                    lines.append(entry)
                except json.JSONDecodeError:
                    continue

        # Trim to limit
        lines = lines[-limit:]

        # Compact format: remove verbose fields, keep essentials
        compact = []
        for e in lines:
            item = {
                "time": e.get("timestamp", "")[:19],  # trim to seconds
                "tool": e.get("tool", "?"),
            }
            # Compact args: skip desktop_id and empty values
            args = e.get("args", {})
            compact_args = {k: v for k, v in args.items()
                           if v and k != "desktop_id"}
            if compact_args:
                item["args"] = compact_args

            if e.get("duration_ms"):
                item["ms"] = e["duration_ms"]

            # Compact result: include but truncate long values
            result = e.get("result")
            if result and result != "[image]":
                if isinstance(result, str) and len(result) > 200:
                    item["result"] = result[:200] + "..."
                elif isinstance(result, dict):
                    # Remove image data, keep structure
                    clean = {k: v for k, v in result.items()
                             if k not in ("image_base64", "image")}
                    item["result"] = clean
                else:
                    item["result"] = result

            if e.get("intent"):
                item["intent"] = e["intent"]

            compact.append(item)

        return json.dumps({
            "desktop_id": desktop_id,
            "entries": len(compact),
            "log_path": str(log_file),
            "logs": compact,
        }, indent=2)
