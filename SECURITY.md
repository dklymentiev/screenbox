# Security Policy

## Reporting Vulnerabilities

If you discover a security vulnerability in Screenbox, please report it responsibly.

**Report:** Use [GitHub private vulnerability reports](https://github.com/dklymentiev/screenbox/security/advisories/new) (preferred).
**Response time:** We aim to acknowledge within 48 hours.

Please include:
- Description of the vulnerability
- Steps to reproduce
- Impact assessment
- Suggested fix (if any)

Do NOT open a public GitHub issue for security vulnerabilities.

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.14.x  | Yes       |
| < 0.14  | No        |

## Security Architecture

Screenbox creates Docker containers with full virtual desktops. Each desktop has:
- Isolated filesystem (no bind mounts to host)
- Network isolation (Docker network, no host network)
- Memory and CPU limits
- Optional Docker API proxy with endpoint whitelist

### Attack Surface

- **MCP API** -- accepts tool calls from AI agents. Protected by API token (Bearer auth).
- **Dashboard** -- web UI for monitoring. Protected by cookie-based auth.
- **Desktop shell** -- `desktop_shell` tool executes commands inside containers. Commands run as unprivileged user inside isolated container.
- **Chrome extension** -- communicates via WebSocket (ws-bridge) inside container. Token-authenticated.

### Recommendations

- **Do not expose MCP API to the public internet.** MCP API is designed for local or VPN access.
- **Use unique API tokens.** Generate with `openssl rand -hex 32`.
- **Run in isolated environments.** Desktops are containers but not sandboxes -- a determined attacker with shell access could attempt container escape.
- **Enable Docker API proxy** for shared/multi-tenant environments (see docker-compose.yml).
- **Review agent actions** via dashboard logs before granting autonomous access.

## Docker API Proxy

Screenbox includes a custom Docker API proxy (`docker-proxy.py`) that restricts
which Docker API endpoints the MCP server can access. This prevents a compromised
MCP process from managing arbitrary containers on the host.

The proxy is optional. For single-user/VPN environments, direct Docker socket
mount is faster. For shared environments, enable the proxy.

## Dependencies

- Docker Engine 20.10+
- Python 3.10+
- Chromium (inside container)
- ImageMagick (inside container, for screenshots)

We recommend keeping all dependencies up to date.
