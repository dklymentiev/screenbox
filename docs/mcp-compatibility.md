# MCP Client Compatibility

Screenbox works with any MCP-compatible client. This document covers tested clients, transport modes, and config examples.

## Transport Modes

| Mode | Flag | Endpoint | Use case |
|------|------|----------|----------|
| stdio | (default) | -- | Local install via pip, single agent |
| Streamable HTTP | `--http` | `/mcp` | Docker Compose, remote access, multi-agent |
| SSE | `--sse` | `/sse` | Legacy clients that do not support streamable HTTP |

Streamable HTTP is stateless and survives restarts. SSE is supported but deprecated.

## Tested Clients

### Claude Desktop

Supports both stdio and streamable HTTP.

**Streamable HTTP (Docker Compose):**

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

**stdio (pip install):**

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

Config location: `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows).

### Claude Code

Supports streamable HTTP and stdio. Add via CLI or config file.

**CLI (streamable HTTP):**

```bash
claude mcp add screenbox --transport http http://localhost:8080/mcp
```

**CLI (stdio):**

```bash
claude mcp add screenbox -- python3 -m screenbox
```

**Manual config (~/.claude.json):**

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

### Cursor

Supports stdio and streamable HTTP. Configure in `.cursor/mcp.json` (project) or Cursor settings UI.

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://localhost:8080/mcp"
    }
  }
}
```

### Other MCP Clients

Any client implementing the MCP specification works with Screenbox. Use streamable HTTP for remote setups, stdio for local.

Generic streamable HTTP config:

```json
{
  "mcpServers": {
    "screenbox": {
      "url": "http://<host>:8080/mcp"
    }
  }
}
```

## Authentication

When running with API tokens, pass credentials via headers:

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

For stdio transport, set `SCREENBOX_API_KEY` environment variable or use the `env` field in client config.

## Choosing a Transport

- **Local, single agent** -- use stdio (pip install, no network)
- **Docker Compose** -- use streamable HTTP (default setup)
- **Remote server / multi-agent** -- use streamable HTTP over VPN or private network
- **Legacy client** -- use SSE if streamable HTTP is not supported
