<!-- Auto-generated -->

# Screenbox User Guide

**Version 0.14.0**

Real desktops for AI agents. Screenbox gives any MCP-compatible AI agent its own
isolated virtual desktop with a real Chromium browser. Agents see, click, type,
and navigate -- just like a human.

---

## 1. Getting Started

### Install and run

```bash
git clone https://github.com/dklymentiev/screenbox.git
cd screenbox
./setup.sh
docker compose up -d
```

Dashboard: `http://localhost:16000` | MCP endpoint: `http://localhost:8080/mcp`

### Connect your MCP client

```json
{
  "mcpServers": {
    "screenbox": { "url": "http://localhost:8080/mcp" }
  }
}
```

For pip install (stdio, no dashboard): `pip install screenbox-mcp`, then use
`{"command": "python3", "args": ["-m", "screenbox"]}` instead of `url`.

### First interaction

Tell your agent "Create a desktop and open github.com":

```
desktop_manage(action="create", desktop_id="work-1")
desktop_chrome(desktop_id="work-1", action="navigate", url="https://github.com")
desktop_screenshot(desktop_id="work-1")
```

Requirements: Docker 20.10+, Python 3.10+, ~2 GB RAM per desktop.

---

## 2. Creating and Managing Desktops

```
desktop_manage(action="create", desktop_id="work-1")
desktop_manage(action="create", desktop_id="wide", resolution="1920x1080")
desktop_manage(action="list")
desktop_manage(action="status", desktop_id="work-1")
desktop_manage(action="pause", desktop_id="work-1")
desktop_manage(action="resume", desktop_id="work-1")
desktop_manage(action="destroy", desktop_id="work-1")
```

**Locking** prevents conflicts when multiple agents share an instance:

```
desktop_manage(action="acquire", desktop_id="work-1")
desktop_manage(action="release", desktop_id="work-1")
desktop_manage(action="smart_acquire")        # find or create available desktop
desktop_manage(action="heartbeat", desktop_id="work-1")  # reset lease timer
```

**Software and apps:**

```
desktop_manage(action="install", desktop_id="work-1", label="vim")
desktop_manage(action="app_launch", desktop_id="work-1", label="chrome", url="https://example.com")
desktop_manage(action="proc_list", desktop_id="work-1")
desktop_manage(action="proc_kill", desktop_id="work-1", label="firefox")
```

**File transfer:**

```
desktop_file(desktop_id="work-1", action="upload", path="/home/user/data.csv", content_base64="...")
desktop_file(desktop_id="work-1", action="download", path="/home/user/report.pdf")
desktop_file(desktop_id="work-1", action="list", path="/home/user/")
desktop_file(desktop_id="work-1", action="upload_tar", content_base64="...", dest_dir="/home/user/project/")
```

---

## 3. Browser Control

Screenbox runs real Chromium with an extension for semantic page access.

**Navigation:**

```
desktop_chrome(desktop_id="work-1", action="navigate", url="https://github.com")
desktop_chrome(desktop_id="work-1", action="back")
desktop_chrome(desktop_id="work-1", action="forward")
```

**Reading content:**

```
desktop_chrome(desktop_id="work-1", action="page_read")     # full page text
desktop_chrome(desktop_id="work-1", action="view_read")     # visible viewport only
desktop_chrome(desktop_id="work-1", action="page_info")     # URL, title, viewport
desktop_chrome(desktop_id="work-1", action="page_map")      # all interactive elements with coords
```

`page_map` returns elements as `{"i": 1, "t": "a", "l": "Sign in", "r": [x, y, w, h]}`.
Click center: `desktop_click(x + w/2, y + h/2)`.

**Interaction:**

```
desktop_chrome(desktop_id="work-1", action="click", selector="Sign in")
desktop_chrome(desktop_id="work-1", action="type", selector="input[name=q]", script="query")
desktop_chrome(desktop_id="work-1", action="search", selector="keyword")
desktop_chrome(desktop_id="work-1", action="eval", script="document.title")
desktop_chrome(desktop_id="work-1", action="extract", script="Extract all prices as JSON")
```

**Tabs:**

```
desktop_chrome(desktop_id="work-1", action="tabs")
desktop_chrome(desktop_id="work-1", action="new_tab", url="https://example.com")
desktop_chrome(desktop_id="work-1", action="switch_tab", selector="0")
desktop_chrome(desktop_id="work-1", action="close_tab")
```

**Cookies:** `cookies`, `set_cookies`, `clear_cookies` actions.

**Waiting:** `desktop_chrome(action="wait_for", selector=".loaded")` or `action="ready"`.

**Other:** `dom`, `pdf`, `network`, `performance`, `console_start/stop/get`, `ssl_errors`.

### The screenshot-look-click workflow

For non-browser windows or when DOM access is unavailable:

```
desktop_screenshot(desktop_id="work-1")                # see screen with grid
desktop_look(desktop_id="work-1", cell=5)              # OCR cell, get real coords
desktop_click(desktop_id="work-1", x=642, y=358)       # click (returns observe image)
```

Never guess coordinates from screenshots -- they are resized. Always use `look`.

**Screenshot options:** `grid=true` (default), `enhance=true` (contrast boost),
`region=[x,y,w,h]` (crop), `cell=5` (single cell).

**Waiting for visual changes:**

```
desktop_wait_stable(desktop_id="work-1", timeout=10)    # wait until screen stops changing
desktop_wait_change(desktop_id="work-1", timeout=15)    # wait until something changes
```

---

## 4. Device Emulation

Standard Chrome DevTools Protocol emulation for testing responsive layouts.

```
desktop_chrome(desktop_id="work-1", action="emulate",
    script='{"width": 375, "height": 812, "deviceScaleFactor": 3, "mobile": true}')
```

Parameters follow CDP `Emulation.setDeviceMetricsOverride`: `width`, `height`,
`deviceScaleFactor`, `mobile`, `userAgent`.

**Geolocation:**

```
desktop_chrome(desktop_id="work-1", action="geolocation",
    script='{"latitude": 37.7749, "longitude": -122.4194, "accuracy": 100}')
```

---

## 5. Screen Recording

```
desktop_shell(desktop_id="work-1",
    command="ffmpeg -f x11grab -i :1 -c:v libx264 /tmp/session.mp4 &")
desktop_manage(action="proc_kill", desktop_id="work-1", label="ffmpeg")
desktop_file(desktop_id="work-1", action="download", path="/tmp/session.mp4")
```

**Replay (beta):** Extract tool calls from session logs and re-execute them as
YAML recipes via `desktop_replay(desktop_id, recipe="my-recipe.yaml")`.

---

## 6. Snapshots

Save and restore full desktop state (files, sessions, apps).

```
desktop_manage(action="snapshot_save", desktop_id="work-1", label="checkpoint")
desktop_manage(action="snapshot_list", desktop_id="work-1")
desktop_manage(action="snapshot_restore", desktop_id="work-1", label="checkpoint")
```

**Clone pattern:** Save a template, create a new desktop, restore into it:

```
desktop_manage(action="snapshot_save", desktop_id="template", label="base")
desktop_manage(action="create", desktop_id="worker-1")
desktop_manage(action="snapshot_restore", desktop_id="worker-1", label="base")
```

Snapshots are stored as tar.gz in `~/.screenbox/snapshots/`.

---

## 7. Knowledge System

Teach Screenbox facts about apps. Knowledge is auto-injected into screenshots
when the matching app is active.

```
desktop_add_knowledge(desktop_id="work-1", app="libreoffice-calc",
    fact="Formula bar is at y=140. Click cell first, then type.",
    triggers=["libreoffice", "calc"], kind="app")
```

- `kind`: `app` (application tips), `flow` (workflow steps), `site` (website-specific)
- `triggers`: window title substrings that activate this knowledge

**Search:**

```
desktop_knowledge_search(desktop_id="work-1", query="libreoffice")
desktop_knowledge_search(desktop_id="work-1", app="libreoffice-calc")
desktop_knowledge_search(desktop_id="work-1")          # list all
```

**HTTP API:** `GET /api/knowledge`, `POST /api/knowledge/{slug}/facts`,
`DELETE /api/knowledge/{slug}`, `POST /api/knowledge/import-server`.

---

## 8. Dashboard

Web UI at `http://localhost:16000` for monitoring desktops.

Features: live VNC/RDP view, desktop lifecycle controls, action log viewer,
knowledge base management, resource monitoring, desktop share links.

The dashboard proxies everything through the MCP HTTP API -- no direct Docker access.

**System tools:**

```
screenbox_info()                                    # architecture and config overview
screenbox_logs(desktop_id="work-1")                 # action history
```

**Share links:** `POST /api/desktop/share`, `GET /api/desktop/share/validate`,
`DELETE /api/desktop/share`.

---

## 9. Security and Authentication

- Do not expose MCP API to the public internet. Use localhost or VPN only.
- Use unique API tokens (`setup.sh` generates them).
- Desktops are isolated containers but not hardened sandboxes.

**Admin access:** Set `SCREENBOX_ADMIN_KEY` or use `SCREENBOX_API_TOKEN` as Bearer token.

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

**Agent registration:**

```
desktop_manage(action="register", agent_id="my-bot", label="My Bot")  # returns api_key
```

**Ownership:** Agent-created desktops belong to that agent. Admin-created desktops
are shared. Agents see only their own + shared desktops.

**Admin API:** `/api/agent/register`, `/api/agent/suspend`, `/api/agent/activate`,
`/api/agent/delete`, `/api/agent/reset-key`, `/api/agent/list`.

---

## 10. Configuration

`~/.screenbox/config.json`:

| Key | Default | Description |
|-----|---------|-------------|
| `max_desktops` | 5 (3 on macOS/WSL2) | Maximum concurrent desktops |
| `memory_per_desktop` | `2048m` | Docker memory limit per container |
| `default_viewport` | `1920x1080` | Screen resolution |
| `idle_pause_minutes` | 20 | Auto-pause inactive desktops (0 = disable) |
| `lease_ttl` | 600 | Seconds before acquired desktop auto-releases |
| `image` | `screenbox:latest` | Docker image for new desktops |
| `chrome_args` | `[]` | Extra Chrome launch arguments |
| `port_bind_address` | `127.0.0.1` | Address to bind container ports |

**Environment variables:** `SCREENBOX_TRANSPORT` (both), `SCREENBOX_HOST` (0.0.0.0),
`SCREENBOX_PORT` (8080), `SCREENBOX_API_TOKEN`, `SCREENBOX_ADMIN_KEY`,
`SCREENBOX_DESKTOP_IMAGE`, `SCREENBOX_DASHBOARD_PORT` (16000).

**Images:** `screenbox:latest` (~920 MB, XFCE) or `screenbox:mate` (~1.7 GB, MATE).

**Remote mode:** `python3 -m screenbox --http` for streamable HTTP transport.

**Upgrading:** `git pull && ./setup.sh`. Recreate desktops after upgrade.

---

## 11. Troubleshooting

**Chrome crashed:** Relaunch with extension:
`desktop_manage(action="app_launch", desktop_id="work-1", label="chrome")`.

**Wrong coordinates:** Never guess from screenshots. Always use `desktop_look(cell=N)`
for real coordinates before clicking.

**Page map empty:** Chrome extension not loaded. Relaunch Chrome, then check:
`desktop_chrome(action="ready")`.

**Speed up interactions:** Use `desktop_batch` to combine actions:

```
desktop_batch(desktop_id="work-1", actions=[
    {"tool": "click", "x": 100, "y": 200},
    {"tool": "type", "text": "hello"},
    {"tool": "key", "keys": "Return"}
])
```

**Window management:** `desktop_window(action="list|activate|maximize|minimize|close")`.

**Clipboard:** `desktop_manage(action="clipboard_get|clipboard_set")`.

**Grid control:** `desktop_manage(action="grid_on|grid_off")`.

**Debug tools** (advanced, not for regular workflows):
`desktop_debug(action="on_screen|a11y_tree|click_text|wait_text|menu_click")`.

**Help:** `desktop_help()` returns workflow reference and patterns.

**Data directory:** `~/.screenbox/` contains `config.json`, `desktops/`, `snapshots/`, `logs/`.
