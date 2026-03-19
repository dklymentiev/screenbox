"""Unified access control for MCP tools and HTTP API.

Single point of authorization -- both channels call the same guard.
Registry (SQLite) is the single source of truth for:
  - Agent identity (who are you?)
  - Sessions (are you authenticated?)
  - Desktop assignments (which desktops can you use?)

Modes:
  strict=False (default): backward-compatible, agent auth is optional
  strict=True (SCREENBOX_REQUIRE_AUTH=true): all access requires valid session
"""

import logging
import os
import threading
from typing import Optional

from .request_context import get_current_role

log = logging.getLogger("screenbox.access")

# Strict mode: require agent auth for all operations (default: enabled)
STRICT = os.environ.get("SCREENBOX_REQUIRE_AUTH", "true").lower() not in ("false", "0", "no")


class AccessGuard:
    """Unified access control for both MCP and HTTP channels.

    Usage:
        guard = AccessGuard(registry)

        # HTTP API: extract from request
        agent_id = guard.authenticate(agent_id="bot1", api_key="xxx")
        guard.check_desktop_access(agent_id, "desktop-1")

        # MCP tools: use connection-level session
        guard.set_session(agent_id="bot1", session_token="xxx")
        guard.check_desktop_access(guard.current_agent, "desktop-1")
    """

    def __init__(self, registry):
        self._registry = registry
        self._lock = threading.Lock()
        # Per-MCP-connection session state
        self._agent_id: Optional[str] = None
        self._session_token: Optional[str] = None

    @property
    def current_agent(self) -> Optional[str]:
        """Current authenticated agent for MCP connection."""
        return self._agent_id

    @property
    def is_authenticated(self) -> bool:
        return self._agent_id is not None

    def set_session(self, agent_id: str, session_token: str):
        """Set MCP connection-level session (called once on connect)."""
        with self._lock:
            self._agent_id = agent_id
            self._session_token = session_token
        log.info("MCP session: agent=%s", agent_id)

    def clear_session(self):
        """Clear MCP session (on disconnect)."""
        with self._lock:
            self._agent_id = None
            self._session_token = None

    def authenticate(self, agent_id: str, api_key: str,
                     source_ip: str = "") -> Optional[str]:
        """Authenticate agent. Returns session_token or None.

        Used by both:
        - HTTP API: POST /api/agent/login
        - MCP: connection init params
        """
        token = self._registry.login(agent_id, api_key, source_ip)
        if token:
            log.info("Agent authenticated: %s", agent_id)
        return token

    def validate_session(self, session_token: str) -> Optional[dict]:
        """Validate session token. Returns {agent_id, source_ip} or None.

        Used by both channels for per-request validation.
        """
        return self._registry.validate_session(session_token)

    def check_desktop_access(self, agent_id: Optional[str],
                             desktop_id: str) -> None:
        """Check if agent can access desktop. Raises ValueError if not.

        Rules:
        1. If strict mode and no agent_id -> deny
        2. If desktop is assigned to another agent -> deny
        3. If desktop is acquired by another agent -> deny
        4. Otherwise -> allow

        Called by both MCP tools and HTTP API handlers.
        """
        # Admin bypasses all access checks
        if get_current_role() == "admin":
            return

        if STRICT and not agent_id:
            raise ValueError(
                "Authentication required. "
                "Register: desktop_manage(action='register', agent_id='your-name') -> get api_key. "
                "Then pass api_key via X-API-Key header."
            )

        if not agent_id:
            return  # non-strict mode, no auth required

        # Check registry assignment (ownership)
        assigned_to = self._registry.get_assignment(desktop_id)
        if assigned_to and assigned_to != agent_id:
            raise ValueError(
                f"Desktop '{desktop_id}' is assigned to agent '{assigned_to}'. "
                f"Only admin can reassign."
            )

    def check_desktop_create(self, agent_id: Optional[str]) -> None:
        """Check if agent can create desktops."""
        if get_current_role() == "admin":
            return
        if STRICT and not agent_id:
            raise ValueError(
                "Authentication required to create desktops. "
                "Register: desktop_manage(action='register', agent_id='your-name') -> get api_key. "
                "Then pass api_key via X-API-Key header."
            )

    def get_agent_desktops(self, agent_id: str) -> list[str]:
        """Get desktops assigned to agent."""
        return self._registry.get_agent_desktops(agent_id)

    def auto_assign(self, desktop_id: str, agent_id: Optional[str]) -> None:
        """Auto-assign desktop to agent on create (if authenticated)."""
        if agent_id:
            self._registry.assign_desktop(desktop_id, agent_id)
            log.info("Auto-assigned %s to %s", desktop_id, agent_id)
