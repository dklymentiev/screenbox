# Screenbox

Real desktops for AI agents.

Screenbox gives any MCP-compatible AI agent (Claude, Cursor, Copilot, etc.) its own isolated virtual desktop with a real Chromium browser. Your agents see, click, type, and navigate -- just like a human would. You watch them work via RDP or VNC. You take control when they need help.

Each desktop is a fully isolated Docker container. No bind mounts -- files move only through explicit API calls. Save and restore state with snapshots. Everything runs on your machine.

## Demo

![Screenbox Demo](https://github.com/user-attachments/assets/0af9f973-fd53-45b5-a951-e81f071a059a)

## Quick Start

### Option A: Docker Compose (recommended)

Full setup with dashboard, multi-desktop support, and web UI.

```bash
git clone https://github.com/dklymentiev/screenbox.git
cd screenbox
./setup.sh          # generates .env, builds desktop image + services
docker compose up -d
```

Dashboard: http://localhost:16000
MCP endpoint: http://localhost:8080/mcp

Add to your MCP client (Claude Desktop, Claude Code, Cursor):

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

### Option B: pip install (single agent, no dashboard)

Lightweight setup -- MCP server runs locally via stdio.

```bash
pip install screenbox-mcp
docker build -f docker/Dockerfile -t screenbox:latest docker/
```

```json
{
  "mcpServers": {
    "screenbox": {
      "command": "python3",
      "args": ["-m", "screenbox"]
    }
  }
}
```

Then tell your agent:

> "Create a desktop and go to github.com"

## Authentication

Screenbox uses API keys for agent identity. Each agent registers once and uses
its key for all subsequent requests.

### Admin Access

Set `SCREENBOX_ADMIN_KEY` in `.env` -- full access to all desktops.
The `SCREENBOX_API_TOKEN` (Bearer token) also grants admin access.

Pass via `X-API-Key` header or `Authorization: Bearer <token>` header.

### Agent Registration

```
1. Register:  desktop_manage(action="register", agent_id="my-bot", label="My Bot")
              -> returns api_key (save it!)

2. Use key:   Pass api_key via X-API-Key header on every request

3. Create:    desktop_manage(action="create", desktop_id="work-1")
              -> desktop owned by "my-bot"

4. Work:      desktop_screenshot("work-1"), desktop_click("work-1", ...) etc.
              -> only "my-bot" can access "work-1"
```

### Ownership Rules

- Desktop created by an agent belongs to that agent (persists across restarts)
- Admin-created desktops are shared (any agent can use them)
- Agents see only their own desktops + shared desktops
- Admin sees and manages all desktops

### MCP Client Config (with auth)

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp",
      "headers": {
        "Authorization": "Bearer <your-api-token>"
      }
    }
  }
}
```

## What Your Agent Can Do

```
Agent: desktop_manage(action="create", desktop_id="browser-1")
Agent: desktop_chrome(desktop_id="browser-1", action="navigate", url="https://github.com")
Agent: desktop_screenshot("browser-1")                           -- sees the page
Agent: desktop_chrome(desktop_id="browser-1", action="page_map") -- structured page content
Agent: desktop_look("browser-1", cell=5)                         -- OCR a grid cell for precise coords
Agent: desktop_click("browser-1", 640, 360)                      -- clicks
Agent: desktop_type("browser-1", "hello world")                  -- types
```

### Chrome Recovery

If Chrome crashes or MCP restarts, relaunch Chrome with the Screenbox extension:

```
Agent: desktop_manage(action="app_launch", app="chrome", app_args="https://example.com")
       -> launched: true, extension_ready: true
```

This uses `start-chrome.sh` which handles singleton locks, service worker cache,
and extension loading automatically.

## Architecture

All desktop operations go through a single path: **MCP server -> manager -> Docker API**.
Dashboard, MCP tools, and HTTP API all use the same `manager.exec()` for screenshots,
shell commands, and container lifecycle. No direct `docker` CLI calls.

A custom Docker API proxy (`docker-proxy.py`) sits between MCP and the Docker daemon,
whitelisting allowed endpoints and properly streaming exec stdout for reliable binary
data transfer (screenshots, file reads).

## Security

Screenbox gives AI agents full desktop access -- browser, shell, files. Run it responsibly:

- **Do not expose MCP API to the public internet.** Use localhost or VPN only.
- **Use unique API tokens.** `setup.sh` generates them automatically.
- **Desktops are isolated containers** but not hardened sandboxes. Do not run untrusted agents without review.
- **Enable Docker API proxy** for shared or multi-tenant environments.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and detailed security architecture.

## Features

- **MCP-native** -- works with Claude Desktop, Claude Code, Cursor, or any MCP client
- **Real Chromium** -- not headless, not Playwright. A real browser with DevTools and extensions
- **Fully isolated** -- each desktop is an isolated Docker container. No bind mounts, no host access
- **Snapshots** -- save and restore desktop state (files, sessions) on demand
- **Observable** -- watch agents work live via RDP or VNC
- **Human-in-the-loop** -- take mouse/keyboard control, help the agent, release control
- **Semantic element map** -- agents get a structured map of all interactive elements with coordinates
- **Cross-platform** -- Linux (native Docker), macOS (Docker Desktop), Windows (WSL2)
- **Lightweight** -- ~2 GB RAM per desktop, no GPU needed
- **Knowledge compilation** -- agents learn from past sessions. Action logs are compiled into reusable knowledge facts that are auto-injected into future interactions

## Knowledge Compilation

Agents lose learned knowledge between sessions. The knowledge compilation pipeline solves this:

```
Session logs (action history)
    |  desktop_compile_knowledge()
    v
Candidate facts (declarative, not imperative)
    |  desktop_merge_knowledge(mode="preview")
    v
Diff: new / updated / unchanged
    |  desktop_merge_knowledge(mode="apply")
    v
Stored knowledge (auto-injected into screenshot/look responses)
```

Configure any OpenAI-compatible LLM in `.env`:
```bash
SCREENBOX_LLM_ENDPOINT=https://openrouter.ai/api/v1
SCREENBOX_LLM_MODEL=google/gemini-2.5-flash
SCREENBOX_LLM_KEY=sk-...
```

## MCP Tools

Screenbox exposes 21 MCP tools: 8 core, 4 dispatchers, 4 knowledge, 2 system, and 1 debug tool.

### Core Tools (8)

| Tool | Description |
|------|-------------|
| `desktop_screenshot` | Capture screen as JPEG (grid overlay, enhance options) |
| `desktop_look` | OCR a grid cell -- get precise text and coordinates for clicking |
| `desktop_click` | Click at (x, y) with observe mode -- returns OCR around click point |
| `desktop_type` | Type text via keyboard |
| `desktop_key` | Key combo (Ctrl+C, Enter, Alt+F4, etc.) |
| `desktop_shell` | Run shell command in container |
| `desktop_batch` | Execute multiple actions in sequence (reduce round-trips) |
| `desktop_help` | Show tool reference and workflow patterns |

### Dispatcher Tools (4)

Each dispatcher consolidates related actions behind a single `action` parameter:

| Tool | Actions |
|------|---------|
| `desktop_chrome` | `navigate`, `page_map`, `page_read`, `view_read`, `cursor_read`, `eval`, `tabs`, `new_tab`, `close_tab`, `switch_tab`, `back`, `forward`, `wait_for`, `screenshot`, `search`, `extract`, `dom`, `page_info`, `cookies`, `set_cookies`, `clear_cookies`, `pdf`, `click`, `type`, `performance`, `network`, `console_start`, `console_stop`, `console_get`, `ready`, `ssl_errors`, `emulate`, `geolocation` |
| `desktop_window` | `list`, `activate`, `minimize`, `maximize`, `restore`, `resize`, `move`, `close`, `show_desktop` |
| `desktop_file` | `upload`, `download`, `list`, `upload_tar` |
| `desktop_manage` | `create`, `destroy`, `list`, `status`, `pause`, `resume`, `acquire`, `release`, `smart_acquire`, `heartbeat`, `health`, `snapshot_save`, `snapshot_restore`, `snapshot_list`, `clipboard_get`, `clipboard_set`, `grid_on`, `grid_off`, `overlay`, `install`, `uninstall`, `app_launch`, `proc_list`, `proc_kill`, `scroll`, `drag`, `mouse_move`, `mouse_down`, `mouse_up`, `right_click`, `wait_window`, `wait_idle` |

### Debug Tools (1)

| Tool | Actions |
|------|---------|
| `desktop_debug` | `on_screen` (AT-SPI/OCR/Vision cascade), `text`, `click_text`, `wait_text`, `element` (AI vision), `hover`, `a11y_apps`, `a11y_tree`, `a11y_find`, `a11y_activate`, `a11y_set_text`, `inspect_cell`, `menu_click` |

Debug tools are for advanced automation and accessibility inspection. Normal agent workflow should use `screenshot -> look -> click`.

### Knowledge Tools (4)

| Tool | Description |
|------|-------------|
| `desktop_add_knowledge` | Teach the agent facts about specific apps (auto-injected into screenshots) |
| `desktop_knowledge_search` | Search or list knowledge. Empty call = list all available knowledge |
| `desktop_compile_knowledge` | Compile session action logs into knowledge facts via LLM |
| `desktop_merge_knowledge` | Preview or apply merge of compiled facts into existing knowledge |

### System Tools (2)

| Tool | Description |
|------|-------------|
| `screenbox_info` | Architecture, config, and running desktops overview |
| `screenbox_logs` | Read action history for a desktop session |

### Workflow: screenshot -> look -> click

The recommended interaction pattern:

```
1. desktop_screenshot("my-desktop")              -- see the full screen
2. desktop_look("my-desktop", cell=5)             -- OCR cell 5 for precise coordinates
3. desktop_click("my-desktop", x=642, y=358)      -- click using coordinates from look
```

`desktop_click` returns an image + OCR around the click point by default (`observe=true`), so you often don't need a separate screenshot after clicking.

## How Page Map Works

`desktop_chrome(action="page_map")` returns semantic page structure -- headings, links, forms -- with viewport coordinates:

```json
{
  "u": "https://github.com",
  "t": "GitHub",
  "v": [1280, 720],
  "n": 42,
  "e": [
    {"i": 1, "t": "a", "l": "Sign in", "r": [1150, 12, 60, 24]},
    {"i": 2, "t": "input", "l": "Search GitHub", "r": [320, 10, 400, 32]},
    {"i": 3, "t": "button", "l": "Search", "r": [730, 10, 50, 32]}
  ]
}
```

Each element has: index (`i`), type (`t`), label (`l`), and viewport rect (`r: [x, y, w, h]`).
Click the center: `desktop_click(x + w/2, y + h/2)`. No vision model needed -- faster and cheaper than screenshot-based agents.

## Architecture

```
MCP Client (Claude, Cursor, any agent)
    |
    | MCP protocol (stdio, streamable-http, or SSE)
    |
Screenbox MCP Server (Python, docker.sock)
    |
    +-- HTTP API (:8080) -- REST + SSE events
    |       |
    |   Dashboard (pure UI, no docker access)
    |       +-- VNC/RDP proxy to desktops
    |       +-- State from MCP SSE events
    |       +-- Screenshots from MCP API
    |
    +-- Desktop 1: Xvnc + xrdp + Chromium + CDP extension
    +-- Desktop 2: ...
    +-- Desktop N: ...
            |
            +-- xrdp (port 3389) -- RDP viewer
            +-- Xvnc (port 5900) -- VNC protocol
            +-- Chrome CDP (port 9222) -- semantics, navigate, eval
            +-- WS bridge (port 8765) -- extension communication
```

## Data & Isolation

Desktops are **fully isolated** -- no bind mounts between container and host. Files only move through explicit API calls.

```
~/.screenbox/
  config.json                         # Settings
  desktops/{id}/                      # Desktop metadata
  snapshots/{id}/snapshot-*.tar.gz    # Saved desktop states
  logs/                               # Action logs
```

**Save state before destroying:**
```
Agent: desktop_manage(action="snapshot_save", desktop_id="browser-1", label="logged-into-github")
Agent: desktop_manage(action="destroy", desktop_id="browser-1")
```

**Restore later:**
```
Agent: desktop_manage(action="create", desktop_id="browser-1")
Agent: desktop_manage(action="snapshot_restore", desktop_id="browser-1")
```

**Clone a desktop:**
```
Agent: desktop_manage(action="snapshot_save", desktop_id="template")
Agent: desktop_manage(action="create", desktop_id="worker-1")
Agent: desktop_manage(action="snapshot_restore", desktop_id="worker-1")
```

## Docker Images

Build the desktop container image (`setup.sh` does this automatically):

```bash
docker build -f docker/Dockerfile -t screenbox:latest docker/
```

| Image | Size | Use case |
|-------|------|----------|
| `screenbox:latest` | ~920 MB | Default -- XFCE desktop + Xvnc + xrdp + Chromium |
| `screenbox:mate` | ~1.7 GB | Full MATE desktop + Chromium + file manager + terminal |

## Configuration

`~/.screenbox/config.json`:

```json
{
  "max_desktops": 5,
  "memory_per_desktop": "2048m",
  "default_viewport": "1920x1080",
  "idle_pause_minutes": 20,
  "lease_ttl": 600,
  "image": "screenbox:latest"
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `max_desktops` | 5 (3 on macOS/WSL2) | Maximum concurrent desktops |
| `memory_per_desktop` | `2048m` | Docker memory limit per container |
| `default_viewport` | `1920x1080` | Screen resolution |
| `idle_pause_minutes` | 20 | Auto-pause inactive desktops (0 = disabled) |
| `lease_ttl` | 600 | Seconds before acquired desktop auto-releases (0 = no expiry) |
| `image` | `screenbox:latest` | Default Docker image for new desktops |
| `chrome_args` | `[]` | Extra Chrome launch arguments |
| `port_bind_address` | `127.0.0.1` | Address to bind container ports |

## Remote Mode (Streamable HTTP)

Run Screenbox as a remote MCP server:

```bash
python3 -m screenbox --http
# or
SCREENBOX_TRANSPORT=streamable-http SCREENBOX_PORT=8080 python3 -m screenbox
```

Connect from any MCP client:

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://your-server:8080/mcp"
    }
  }
}
```

Streamable HTTP is stateless -- survives container restarts without breaking client connections. SSE (`--sse`, `/sse` endpoint) is also supported but deprecated.

### Docker Compose

```bash
./setup.sh           # one-time: generates .env, builds all images
docker compose up -d # start MCP server + dashboard
```

`setup.sh` generates an API token, creates data directories, and builds the desktop image. After setup, `docker compose up -d` is all you need.

The MCP server has direct docker.sock access and acts as the single controller for all desktop operations. The dashboard is a pure UI that proxies everything through the MCP HTTP API.

For reverse proxy setups, see the [Docker Compose documentation](https://docs.docker.com/compose/networking/).

## Upgrading

```bash
git pull
./setup.sh
```

`setup.sh` detects update vs first install automatically. On update it rebuilds all images, restarts services, and tells you to recreate desktops.

After update, recreate desktops (old containers use old image) via dashboard UI or API.
```

Old Docker images are preserved (untagged as `<none>`). Only `docker image prune` removes them.

## Requirements

- Docker 20.10+
- Python 3.10+
- 2 GB RAM per desktop (minimum)
- `--shm-size=512m` for Chrome (handled automatically)

## vs Alternatives

| | Screenbox | Browserbase | Browser MCP | Computer Use |
|---|-----------|-------------|-------------|--------------|
| Full desktop | Yes | No (browser only) | No (bridge) | Yes (cloud) |
| Self-hosted | Yes | No (SaaS) | Yes | No |
| MCP-native | Yes | Yes | Yes | No |
| Container isolation | Yes | Cloud | No | Cloud |
| Persistent state | Yes (snapshots) | No | Shared browser | No |
| Observable (live) | Yes (RDP/VNC) | No | No | No |
| Open source | AGPL-3.0 | Partial | Yes | No |
| Semantic map | Yes (DOM) | Yes (AI) | No | No (vision) |

## License

AGPL-3.0 -- see [LICENSE](LICENSE)

## Links

- Website: [screenbox.dev](https://screenbox.dev)
