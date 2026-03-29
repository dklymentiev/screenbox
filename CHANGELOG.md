# Changelog

## 0.16.0 (2026-03-29) -- Persistence & Cross-Platform Build

### Desktop Persistence
- **Browser history survives restart** -- periodic backup (every 30s) using SQLite backup API with `nolock=1` to read Chrome's locked databases. Restore on container start with `exit_type=Normal` to prevent Chrome crash recovery wipe.
- **Installed packages auto-restore** -- `desktop_manage(action="install")` records packages in `~/.screenbox/installed-packages.txt`. Entrypoint re-installs missing packages on container start (as root, before dropping to screenbox user).
- **Browser profile migration safety** -- Chrome-to-Chromium profile migration only runs when Google Chrome binary is not installed (previously deleted Chrome profile on every start).
- **XFCE settings preserved** -- `cp -n` instead of `cp -f` for default configs, user customizations survive restarts.
- **Graceful Chrome shutdown** -- entrypoint sends SIGTERM to Chrome and waits up to 5s before force kill.

### Build & Setup
- **One-command setup** -- `setup.ps1` (Windows) and `setup.sh` (Linux/macOS) build all images, start services, create demo desktop. Full progress output with elapsed time per step.
- **Docker build DNS fix** -- `dns: [8.8.8.8, 1.1.1.1]` in docker-compose.yml for all services. Resolves build hangs on Windows/WSL2.
- **BuildKit OOM fix** -- MCP and dashboard images build with `DOCKER_BUILDKIT=0` (legacy builder). BuildKit OOM-kills tesseract-ocr install on systems with limited memory.
- **Split MCP Dockerfile** -- monolithic `RUN` with 3x apt-get split into 4 separate layers for better caching and lower peak memory.
- **Apple Silicon support** -- `FROM --platform=linux/amd64` in desktop Dockerfile ensures consistent builds on ARM Macs via Rosetta.
- **Docker preflight check** -- setup scripts verify Docker daemon is running before attempting builds.

### Dashboard
- **Two-step destroy** -- destroy removes container but keeps data (state -> "saved"). Saved desktops shown as cards with Launch/Delete buttons. Delete data removes home volume + dossier permanently. No more orphaned data on disk.
- **VNC thumbnails** -- live VNC streams replace screenshot polling for desktop cards. CPU usage dropped from 84% to 0.3%.
- **Overlay toggles** -- trail/dots toggle switches in preview toolbar.
- **Show saved state persisted** -- toggle state saved in localStorage.
- **Removed ghost image option** -- `screenbox:xfce` dropdown removed, only `screenbox:latest` available.
- **Duplicate card fix** -- pending desktop cards no longer duplicate when SSE event arrives before refresh cycle.

### MCP Server
- **Health check via Docker API** -- image existence check uses httpx to Docker API (works through tcp proxy) instead of subprocess `docker image inspect`.
- **cgroup v1/v2 stats** -- CPU/RAM stats work on both cgroup versions (Docker Desktop, older kernels).
- **Memory limit increased** -- 512MB to 2GB (MCP with tesseract needs more under load).
- **Ghost cleanup safety** -- skips cleanup if `docker ps` returns empty (prevents deleting all desktops when proxy is not ready).
- **MCP session stability** -- removed `stateless_http=True` that caused session drops.

### Docker Proxy
- **Image inspect endpoint** -- added `GET /v1.43/images/{name}/json` to proxy whitelist for health checks.

### Infrastructure
- **Desktop DNS** -- `--dns 8.8.8.8` on `docker create` for reliable package installs inside desktops.
- **gnome-keyring in image** -- pre-installed for Chrome cookie encryption, auto-unlocked with empty password.
- **Click indicator default config** -- writes config on start so indicator renders immediately.
- **Integration test fix** -- replaced `python3 -c` JSON parsing with `grep` (works on Windows without Python).

## 0.15.0 (2026-03-22) -- Knowledge Compilation Pipeline

### Knowledge Compilation
- **Compile session logs into knowledge** -- LLM analyzes action logs and extracts declarative knowledge facts (what the agent learned about apps, OS, and workflows)
- **MCP tools** -- `desktop_compile_knowledge(desktop_id, session_id, task)` and `desktop_merge_knowledge(desktop_id, facts, target, mode)` for agents to compile and merge knowledge programmatically
- **HTTP API** -- `POST /api/knowledge/compile` and `POST /api/knowledge/merge` for dashboard integration
- **Preview + Apply workflow** -- preview merge diff (new/updated/unchanged facts) before applying changes
- **Configurable LLM** -- any OpenAI-compatible endpoint via `SCREENBOX_LLM_ENDPOINT`, `SCREENBOX_LLM_MODEL`, `SCREENBOX_LLM_KEY` env vars (OpenRouter, Ollama, Claude API, etc.)

### Knowledge Quality
- **Declarative facts only** -- compilation prompt enforces declarative style ("dialog dismissed with Enter") instead of imperative ("press Enter to close dialog"), preventing agents from executing hints as commands
- **Three knowledge levels** -- os (desktop environment), app (application-specific), flow (multi-step processes)
- **Trigger-based deduplication** -- preview_merge compares candidates vs existing facts by trigger overlap to prevent duplicates

### Agent Workflow
- **Intent parameter mandatory** -- MCP instructions now require `intent` on every tool call, explaining WHY the action is performed. Logged for knowledge compilation quality.

## 0.14.0 (2026-03-18) -- Shared volume data architecture, desktop profiles

### Data Architecture
- **Shared volume for all desktop data** -- single `screenbox-data` volume mounted in MCP, desktop containers, and dashboard
- **Desktop dossier pattern** -- `desktops/{id}/` holds profile.json, meta.json, recordings/, knowledge/
- **Removed docker cp** -- profile.json written to shared volume before container start, no proxy dependency
- **Removed host bind mounts** -- recordings stored in shared volume, cross-platform compatible (Linux/macOS/Windows)
- **Dashboard reads from shared volume** -- no more separate bind mounts for desktops/recordings

### Desktop Profiles
- **Profile delivery via shared volume** -- MCP writes profile, entrypoint reads from `$SCREENBOX_PROFILE` env var
- **Profile Extension baking** -- profile data embedded into inject script at boot (fixes race condition)
- **User-Agent quoting fix** -- `--user-agent` passed as single quoted argument, no more word-splitting
- **Device emulation improvements** -- Chrome DevTools Protocol overrides for viewport, user-agent, and device metrics (cross-platform testing)
- **Cross-platform testing** -- emulate different devices for UI/compatibility testing

### Recordings
- **Recordings in shared volume** -- FFmpeg writes to `desktops/{id}/recordings/`, dashboard reads same path
- **Large screen guard** -- screens >1920x1080 get clear error instead of silent corrupt MP4 files (#728)
- **Dashboard recording routes** -- backward compatible, checks new and old paths

### Fixes
- Fixed `import os` shadowing in manage.py that broke share/snapshot actions
- Fixed `profile_json` parameter rename caused by replace_all
- Fixed recording filename extraction regex for new paths

## 0.13.6 (2026-03-18) -- Security hardening, desktop lock, browser detect

### Security (Incident Response)
- **Destroy/pause/resume require authentication** -- unauthenticated requests blocked (#718)
- **Desktop lock/unlock** -- protect desktops from accidental deletion. Lock requires auth, unlock requires admin (#707)
- **Source IP in action logs** -- every tool call logs client IP for forensics (#719)
- **Snapshot verification** -- `snapshot_saved` now checks file exists, not just request parameter (#715)

### Browser
- **Auto-detect Google Chrome vs Chromium** in entrypoint.sh and start-chrome.sh (#714)
- **Removed Chrome restrictions** -- no more --disable-sync, --disable-extensions-except
- **Extension symlink on Desktop** for manual Chrome install (Load unpacked)
- **pgrep checks both chromium and chrome** process names

### Infrastructure
- **Auto-snapshot every 15 min** for running desktops (config: auto_snapshot_minutes=15)
- **Disk usage via manager.exec** instead of docker CLI subprocess (#693)

## 0.13.5 (2026-03-18) -- Stateless sessions, Docker sync, stability

### Critical Fixes
- **Stateless HTTP sessions** (`stateless_http=True`) -- MCP restart no longer kills agent sessions (#701)
- **Periodic Docker sync** every 30s -- removes ghost desktops from dashboard, updates states (#708)
- **VNC reconnect** skips non-running desktops -- no more infinite reconnect loops (#698)
- **Knowledge search** returns full fact texts on empty query + fallback to global knowledge (#700)
- **SECURITY.md** added with vulnerability reporting policy and security architecture

### Per-component versioning
- MCP server: 0.13.5
- Dashboard: 0.13.5

## 0.13.4 (2026-03-18) -- Container limits, knowledge flows, bugfixes

### Configuration
- Max desktops increased from 5 to 10
- Container create timeout increased from 30s to 120s

### Knowledge
- Demo LibreOffice Calc flow (6 facts): install, paste data, save as xlsx
- Demo Form Filling flow updated with explicit submit step

### Known Issues
- VNC WebSocket reconnect loop on dashboard (#698)
- Desktop create timeout for remote agents (#699)
- Knowledge list_all doesn't fallback to global flows (#700)

## 0.13.3 (2026-03-17) -- Dashboard screenshot fix, unified architecture

### Architecture
- Dashboard `/api/desktop/screenshot` now uses `manager.exec` (same as MCP tools) instead of direct `docker exec` + `docker cp` via subprocess
- One screenshot path for all consumers: MCP tools, HTTP API, dashboard
- Removed `_screenshot_safe()` workaround (docker cp blocked by proxy whitelist)

### Eval CSP Guidance
- `desktop_chrome(action="eval")` blocked by CSP now returns guidance to use semantic tools (page_read, page_map, type, click)

## 0.13.2 (2026-03-17) -- Custom Docker proxy, screenshot reliability

### Docker Proxy
- **Custom Python Docker API proxy** replaces socat/tecnativa proxy which dropped binary stdout from exec (screenshots failed 60-70%)
- Endpoint whitelist for security (same as tecnativa: containers, exec, images, networks, volumes)
- Proper exec/start raw stream relay via `select()` -- no data loss
- Screenshot now works **10/10** through proxy (was 3-4/10 with socat)
- New files: `docker/docker-proxy.py`, `docker/proxy/Dockerfile`

### Screenshot Diagnostics
- `_exec_bytes` logs stderr and return code on empty stdout
- `_screenshot_raw` logs DISPLAY value on failure
- Removed false "ImageMagick not found" diagnostic

## 0.13.1 (2026-03-17) -- Chrome lifecycle reliability

### Chrome Lifecycle (Epic #93)
- **Extension reconnect** after MCP restart -- stale socket detection via `getpeername()` + `MSG_PEEK`
- **`app_launch(app="chrome")`** now uses `start-chrome.sh` -- launches Chromium with Screenbox extension, verifies process + extension ping, returns honest `launched: false` when binary missing
- **`navigate`** checks browser is running before xdotool fallback -- returns `success: false` instead of silently typing into nothing
- **`shell()`** auto-sets `DISPLAY=:99` via env prefix
- **`page_map`** returns instantly on `about:blank` / `chrome://newtab` instead of 20s timeout
- **Screenshot diagnostics** -- empty bytes now report reason (X display, ImageMagick availability)
- **Better error messages** -- "Browser is not running. Launch it: `app_launch(app='chrome')`"
- New file: `docker/start-chrome.sh` -- single source of truth for Chrome launch with extension

### Knowledge & Dashboard
- Knowledge API auto-detects `kind` (flow/site/os) instead of defaulting to app
- Dashboard proxy no longer hardcodes `kind=app` in GET/DELETE requests
- Eval CSP errors return guidance to use Chrome semantic tools instead

## 0.13.0 (2026-03-17) -- Per-request agent auth

### Auth Redesign
- **Per-request identity** via `X-API-Key` header or Bearer token (no more per-connection sessions)
- **Admin key** (`SCREENBOX_ADMIN_KEY` env) grants full access to all desktops
- `SCREENBOX_API_TOKEN` (Bearer) also treated as admin for backward compatibility
- Admin bypasses all ownership/assignment checks
- New files: `request_context.py` (contextvar identity), `auth_middleware.py` (Starlette middleware)
- Identity resolves: per-request contextvar > MCP guard session > env var
- Stdio transport auto-authenticates from env at startup

### Agent Onboarding
- Register: `desktop_manage(action="register", agent_id="my-bot")` -> get api_key
- Pass api_key via `X-API-Key` header on every request
- Desktop created by agent is auto-assigned to that agent (owner = creator)
- Admin-created desktops are shared (accessible to all)
- Agents can only access own desktops + shared desktops
- Admin can access/manage all desktops

### Improved Logging
- Extension client logs now include host:port for connection debugging
- page_map errors include full traceback

### Per-component versioning
- MCP server: 0.13.0
- Dashboard: 0.12.0

## 0.12.0 (2026-03-15) -- Desktop sharing, dual transport, security

### Desktop Sharing
- View-only share links via `/s/{token}` with configurable TTL (default 1 hour)
- Server-side RFB protocol filtering: drops KeyEvent, PointerEvent, ClientCutText
- Token re-validation every 30s during active VNC sessions (revoke takes effect promptly)
- Agent creates shares for own desktops (session_token), admin for any (Bearer token)
- MCP tool: `desktop_manage(action="share"/"unshare")`
- Dashboard: share button on cards, clipboard copy, toast notification
- New file: `dashboard/share.html` -- public view-only VNC viewer with expiry countdown
- API: POST/DELETE `/api/desktop/share`, GET `/api/desktop/shares`, GET `/api/desktop/share/validate`

### Dual Transport (`SCREENBOX_TRANSPORT=both`)
- **Breaking:** Default transport changed from `streamable-http` to `both`
- `/mcp` -- streamable-http for new MCP clients (Claude Code 2.1.76+)
- `/sse` + `/messages/` -- SSE for legacy MCP clients
- `/api/*` -- HTTP API for all clients
- One port, one server, one process -- no extra overhead
- Removed `--http` hardcode from Dockerfile CMD; transport controlled by env var only

### Security (from audit)
- **[CRITICAL fix]** Server-side view-only enforcement via RFB message filtering in share VNC proxy
- **[HIGH fix]** Active session expiry: token re-validated every 30s, WebSocket closed on revoke
- HTML escaping in error pages (XSS prevention)
- URL sanitization: `X-Forwarded-Host` no longer trusted for share URL construction
- `urllib.parse.urlencode` for query params in mcp_client
- `PYTHONDONTWRITEBYTECODE=1` in dashboard Dockerfile (prevents stale .pyc issues)

### Cleanup
- Standardized on `SCREENBOX_API_TOKEN` everywhere (removed `SCREENBOX_MCP_API_TOKEN`)
- New `mcp_delete()` helper in dashboard mcp_client

### Per-component versioning
- MCP server: 0.12.0
- Dashboard: 0.12.0

## 0.11.0 (2026-03-14) -- MCP as single controller

**Breaking:** Docker socket proxy removed. MCP server now has direct docker.sock access.
Dashboard is docker-free -- all operations proxy through MCP HTTP API.

### Architecture
- MCP HTTP API: REST endpoints for all desktop lifecycle operations (/api/desktop/*)
- SSE event stream: real-time state changes via GET /api/events
- Dashboard proxies create/destroy/control/record through MCP API
- Dashboard state from MCP SSE (no more docker ps polling)
- Screenshots fetched from MCP API (no more docker exec in dashboard)
- docker-socket-proxy service removed from docker-compose.yml
- Docker CLI removed from dashboard Docker image (~40MB smaller)

### New MCP API endpoints
- POST /api/desktop/create, /destroy, /control, /record
- GET /api/desktop/list, /status, /screenshot, /record, /recordings, /ip
- POST /api/desktop/input (xdotool proxy for dashboard remote control)
- GET /api/events (SSE stream with keepalive)
- GET /api/health

### New files
- `src/screenbox/events.py` -- EventBus pub/sub for SSE
- `dashboard/mcp_client.py` -- async HTTP client for MCP API
- `dashboard/mcp_events.py` -- SSE consumer + screenshot loop

### Dashboard UI
- Recording player as overlay modal (click outside or Esc to close)
- Modal confirmation dialog for recording delete (replaces browser alert)
- Recordings tab CSS fix (content no longer bleeds under VNC stream)
- Styled thin scrollbars for logs and recordings lists

### Security
- MCP API requires SCREENBOX_API_TOKEN (was open without it)
- Dashboard cookie-based auth (HttpOnly, SameSite=Strict) replaces ?token= in URL
- VNC/RDP proxy routes now require authentication
- ws-bridge: timing-safe token comparison (hmac.compare_digest)
- .mcp.json added to .dockerignore (prevent token leak into image)

### Setup & Update
- setup.sh: handles both install and update, cross-platform (Linux/macOS/Windows)
- setup.sh generates .mcp.json for Claude Code auto-discovery
- setup.sh auto-detects install vs update, runs docker compose down/up
- Simplified upgrade: `git pull && ./setup.sh`

### Knowledge
- Renamed desktop_search_knowledge -> desktop_knowledge_search
- Empty call returns full list of all knowledge files
- Knowledge select button (browse server files) + upload button (local machine)
- Knowledge directory shared between MCP and dashboard

### MCP Instructions
- Chrome semantics (page_read/page_map) as priority #1 for browser content
- GUI app readiness rules: verify window loaded before typing/pasting
- Shell cat > file preferred over GUI paste for saving text

### Bug fixes
- Chrome + extension always start (not only when CHROME_URL is set)
- Service Worker cache cleared before Chrome launch (stale token fix)
- Auto-connect recovered desktops to managed network (VNC proxy fix)
- Fix destroy API parameter name (save_snapshot -> auto_snapshot)
- Fix get_desktops() mutation bug (shallow copy mutated cache dicts)
- Fix NameError in knowledge import (path -> rel_path)
- Recordings mount changed from read-only to read-write (delete support)
- Recording button visibility (add 'available' field to status response)
- Recordings path sync between MCP and dashboard (SCREENBOX_RECORDINGS_HOST_DIR)
- Default image screenbox:xfce -> screenbox:latest
- Manager: added stop() and start() methods

### Per-component versioning
- MCP server: 0.11.0
- Dashboard: 0.11.0
- Desktop image: 0.10.1 (Chrome always starts, SW cache fix)

## 0.10.0 (2026-03-14) -- Knowledge v2 + Dashboard WebSocket

- Knowledge system v2: 4 types (app, os, flow, site) with cross-references
- Dashboard WebSocket real-time state updates (replaces 3s polling)
- Server-side state overrides with auto-clear for smooth transitions
- Docker LABEL org.screenbox.desktop.version on desktop images

## 0.9.2 (2026-03-13) -- Agent auth + coverage

- Capability-based agent auth: HMAC-SHA256 signed tokens with desktop ACLs
- Token format: {agent, desktops[], exp?} -- stateless, no database
- CLI: `python -m screenbox token create --agent X --desktops a,b,c [--exp 3600]`
- Wildcard access: `--desktops '*'` for admin tokens
- Auth is opt-in: enabled via SCREENBOX_MASTER_SECRET env var
- Auto-generated master secret at ~/.screenbox/master-secret
- Neighbor cell hints in desktop_look: suggests adjacent cells when elements are at edge
- New tests: auth (25), grid (15), input (20), browser (34), vision (16), ocr (26) -- 349 unit + 90 integration = 439 total

## 0.9.1 (2026-03-13) -- Bug fixes + open-source prep

- Fix: desktop_manage(action="create") -32602 error -- accept null params from MCP clients
- Fix: MCP auto-discovers dashboard-created desktops (no restart needed)
- Fix: DNS rebinding protection disabled for network deployments (SCREENBOX_HOST != 127.0.0.1)
- License changed from Apache-2.0 to AGPL-3.0
- Added CONTRIBUTING.md
- Added GitHub Actions CI (lint + test on Python 3.10/3.11/3.12)
- Return type hints on all public methods
- README updated for AGPL-3.0
- 213 unit tests passing

## 0.9.0 (2026-03-13) -- Pre-release hardening

- Streamable HTTP transport (stateless, survives container restarts)
- Encrypted snapshots with age + auto-snapshot timer
- Session tokens, lease TTL, smart acquire for multi-agent
- Desktop recovery: MCP picks up externally-created containers
- Security: shlex.quote injection fixes, mandatory API token, docker socket proxy
- Renamed desktop_find -> desktop_debug (hidden from agents)
- Named Docker volumes for persistent desktop data
- 149 unit tests passing

## 0.8.0 (2026-03-11) -- MATE + XFCE dual image

- MATE desktop image (screenbox:mate, full DE)
- XFCE lightweight image (screenbox:xfce, ~920MB)
- Configurable image per desktop
- Dashboard create form with image/resolution selection
- Smoke tests for both images
- Action replay prototype (beta)

## 0.7.0 (2026-03-10) -- Agent workflow polish

- screenbox_info and screenbox_logs tools
- Knowledge injection: auto-hints based on OCR context
- Grid state persists across Desktop instances
- Workflow hints in screenshot/look responses
- Click observe mode (image+OCR after click)
- Window API: minimize/activate fixes, DE internals filtered

## 0.6.0 (2026-03-08) -- XFCE desktop

- Replace MATE with XFCE for lightweight desktop
- Screenbox dark theme (custom xfwm4 + gtk.css)
- Single-port dashboard (HTTP+WS on 16000)
- Removed Papirus icons (-202MB), use Adwaita
- Chromium launch fixes (wrapper, stale locks, extension)

## 0.5.0 (2026-03-07) -- Xvnc + xrdp

- Replace KasmVNC with Xvnc (TigerVNC) + xrdp
- Screenshot 1000x1000 normalization
- Blue-white click indicator, gaze overlay
- Tesseract OCR in MCP server image
- Unified Docker network

## 0.4.0 (2026-03-06) -- Architecture v2

- Clean architecture migration from monolithic mcp_server.py
- Pre-release security fixes, mixin split
- Docker-compose split (MCP + dashboard)
- Branded error pages, dark overlay theme
