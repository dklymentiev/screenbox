"""Per-request identity context using contextvars.

Sets agent identity for the duration of each HTTP/MCP request.
Works with both asyncio (HTTP transport) and threading (stdio).

Usage in tool handlers:
    from .request_context import get_current_agent, get_current_role
    agent_id = get_current_agent()   # "bot-1" or None
    role = get_current_role()         # "agent" or "admin"
"""

import contextvars
import hashlib
import logging
import os
from typing import Optional

log = logging.getLogger("screenbox.auth")

# Per-request identity (set by middleware, read by tool handlers)
_current_agent: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "current_agent", default=None
)
_current_role: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_role", default="anonymous"
)
_current_ip: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_ip", default=""
)

# Admin keys from environment (set once at startup)
ADMIN_KEY: str = os.environ.get("SCREENBOX_ADMIN_KEY", "")
# API token also grants admin (used by dashboard and MCP clients)
_API_TOKEN: str = os.environ.get("SCREENBOX_API_TOKEN", "")


def get_current_agent() -> Optional[str]:
    """Get agent_id for current request, or None if unauthenticated.

    For stdio transport (single agent), falls back to SCREENBOX_AGENT_ID env
    or treats admin key holder as 'admin'.
    """
    return _current_agent.get()


def get_current_role() -> str:
    """Get role for current request: 'admin', 'agent', or 'anonymous'."""
    return _current_role.get()


def is_admin() -> bool:
    """Check if current request has admin privileges."""
    return _current_role.get() == "admin"


def get_current_ip() -> str:
    """Get source IP for current request."""
    return _current_ip.get()


def set_identity(agent_id: Optional[str], role: str = "agent", ip: str = "") -> None:
    """Set identity for current request context."""
    _current_agent.set(agent_id)
    _current_role.set(role)
    if ip:
        _current_ip.set(ip)


def clear_identity() -> None:
    """Clear identity after request."""
    _current_agent.set(None)
    _current_role.set("anonymous")
    _current_ip.set("")


def resolve_api_key(api_key: str, registry) -> tuple[Optional[str], str]:
    """Resolve API key to (agent_id, role).

    Checks admin key first, then registry.
    Returns (None, 'anonymous') if key is invalid.
    """
    if not api_key:
        return None, "anonymous"

    # Check admin keys (dedicated admin key or API token)
    if ADMIN_KEY and api_key == ADMIN_KEY:
        return "admin", "admin"
    if _API_TOKEN and api_key == _API_TOKEN:
        return "admin", "admin"

    # Check registry
    key_hash = hashlib.sha256(api_key.encode()).hexdigest()
    row = registry._get_conn().execute(
        "SELECT id, status FROM agents WHERE api_key_hash = ?", (key_hash,)
    ).fetchone()

    if row and row["status"] == "active":
        return row["id"], "agent"

    return None, "anonymous"
