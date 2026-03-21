# Screenbox

Real desktops for AI agents.

Screenbox gives any MCP-compatible AI agent (Claude, Cursor, Copilot, etc.) its own isolated virtual desktop with a real Chromium browser. Your agents see, click, type, and navigate -- just like a human would. You watch them work via RDP or VNC. You take control when they need help.

Each desktop is a fully isolated Docker container. No bind mounts -- files move only through explicit API calls. Save and restore state with snapshots. Everything runs on your machine.

## The Problem

AI agents can write code, answer questions, and process data. But they can't click a button, fill a form, or read what's on screen.

Browser automation tools (Playwright, Puppeteer) work for scripted tasks. But when an AI agent needs to figure out what to click -- navigate an unfamiliar UI, handle popups, recover from errors -- scripted automation breaks down.

You need to give the agent actual eyes and hands.

## What Screenbox Does

Screenbox creates real Linux desktops inside Docker containers and connects them to your AI agent via MCP. The agent gets tools to take screenshots, read page structure, click, type, and run shell commands. You get a live video feed of everything the agent does.

Tell your agent:

> "Go to GitHub, find the top trending repos this week, and save them to a spreadsheet"

The agent creates a desktop, opens Chrome, navigates to GitHub Trending, reads the page with `page_map`, opens LibreOffice Calc, and fills in the data. You watch it work live via RDP.

No scripting. No hard-coded selectors. The agent figures it out.

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

## Use Cases

- **AI agent development** -- test and debug MCP-compatible agents in isolated environments
- **QA automation** -- run visual regression tests with real browsers
- **Cross-platform testing** -- verify web applications across device profiles
- **Demo environments** -- create reproducible desktop environments for demonstrations

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

## What Makes Screenbox Different

**Why not headless browsers?**
Headless browsers run in a detectable mode that many sites actively block. They can't handle CAPTCHAs, struggle with cookie consent banners, and fail on sites that fingerprint the browser environment. Screenbox runs a real Chromium instance in a real desktop session -- the same thing a human would see.

**Why not cloud sandboxes?**
Cloud-hosted desktop services send your data -- screenshots, credentials, session state -- to someone else's infrastructure. Screenbox runs entirely on your machine. Your data never leaves your network.

**Why not just Playwright MCP?**
Playwright controls a browser. Screenbox controls an entire desktop. Your agent can open LibreOffice, use the terminal, manage files, switch between applications, and interact with any GUI software -- not just web pages.

## How It Works: screenshot -> look -> click

The core interaction pattern:

```
1. desktop_screenshot("my-desktop")              -- see the full screen
2. desktop_look("my-desktop", cell=5)             -- OCR cell 5 for precise coordinates
3. desktop_click("my-desktop", x=642, y=358)      -- click using coordinates from look
```

`desktop_click` returns an image + OCR around the click point by default (`observe=true`), so you often don't need a separate screenshot after clicking.

For web pages, the agent can skip OCR entirely:

```
Agent: desktop_chrome(desktop_id="browser-1", action="page_map") -- structured page content
Agent: desktop_click("browser-1", 640, 360)                      -- click element by coordinates
```

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

## MCP Tools

Screenbox exposes 19 MCP tools: 8 core, 4 dispatchers, 2 knowledge, 2 system, and 1 debug tool.

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

| Tool | Description |
|------|-------------|
| `desktop_chrome` | Browser control -- navigate, page_map, page_read, tabs, cookies, eval, search, and 20+ more actions. See docs for full list. |
| `desktop_window` | Window management -- list, activate, minimize, maximize, resize, move, close, show_desktop |
| `desktop_file` | File transfer -- upload, download, list, upload_tar |
| `desktop_manage` | Desktop lifecycle -- create, destroy, snapshots, clipboard, scroll, drag, mouse control, process management, and 25+ more actions. See docs for full list. |

### Knowledge Tools (2)

| Tool | Description |
|------|-------------|
| `desktop_add_knowledge` | Teach the agent facts about specific apps (auto-injected into screenshots) |
| `desktop_knowledge_search` | Search or list knowledge. Empty call = list all available knowledge |

### System Tools (2)

| Tool | Description |
|------|-------------|
| `screenbox_info` | Architecture, config, and running desktops overview |
| `screenbox_logs` | Read action history for a desktop session |

### Debug Tools (1)

| Tool | Description |
|------|-------------|
| `desktop_debug` | Advanced automation and accessibility inspection -- on_screen detection, text search, a11y tree, element inspection. For development use. |

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
- **Desktop profiles are for testing only.** User-agent, viewport, and device emulation replicate target environments for QA. Do not use profiles to circumvent website access controls or terms of service.
- **Respect website terms of service.** Agents interacting with third-party websites must comply with those sites' terms of use and robots.txt policies.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and detailed security architecture.

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

### Chrome Recovery

If Chrome crashes or MCP restarts, relaunch Chrome with the Screenbox extension:

```
Agent: desktop_manage(action="app_launch", app="chrome", app_args="https://example.com")
       -> launched: true, extension_ready: true
```

This uses `start-chrome.sh` which handles singleton locks, service worker cache,
and extension loading automatically.

## Upgrading

```bash
git pull
./setup.sh
```

`setup.sh` detects update vs first install automatically. On update it rebuilds all images, restarts services, and tells you to recreate desktops.

After update, recreate desktops (old containers use old image) via dashboard UI or API.

Old Docker images are preserved (untagged as `<none>`). Only `docker image prune` removes them.

## Requirements

- Docker 20.10+
- Python 3.10+
- 2 GB RAM per desktop (minimum)
- `--shm-size=512m` for Chrome (handled automatically)

## Data & Privacy

Screenshots, recordings, and snapshots may capture personal data visible on screen.
When using Screenbox in environments subject to GDPR, CCPA, or similar privacy regulations:

- Ensure you have a lawful basis for processing any personal data captured
- Screenshots and recordings are stored locally (no external transmission)
- Use snapshot encryption for sensitive sessions (`SCREENBOX_ENCRYPT_SNAPSHOTS=true`)
- Delete desktop data when no longer needed

Screenbox does not collect telemetry or transmit data to external services.

## Disclaimer

This software is provided "as is" without warranty of any kind. Desktops are Docker containers
with standard isolation -- they are not security sandboxes. Do not run untrusted code or agents
without review. Users are solely responsible for compliance with applicable laws when using this
software. See LICENSE for full terms.

## License

AGPL-3.0 -- see [LICENSE](LICENSE)

---

Created by [Dmytro Klymentiev](https://klymentiev.com)
