"""Starlette middleware that extracts agent identity from request.

Supports X-API-Key header, Authorization: Bearer, and ?token= query param.
Sits in front of both MCP transport and HTTP API routes.
Sets per-request identity via contextvars so tool handlers can read it.
"""

import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .request_context import resolve_api_key, set_identity, clear_identity

log = logging.getLogger("screenbox.auth")


class AgentAuthMiddleware(BaseHTTPMiddleware):
    """Extract X-API-Key from request, resolve to agent identity."""

    def __init__(self, app, registry):
        super().__init__(app)
        self._registry = registry

    async def dispatch(self, request: Request, call_next) -> Response:
        # Auth resolution order: X-API-Key header > Bearer token > ?token= query param
        api_key = request.headers.get("X-API-Key", "")
        if not api_key:
            auth = request.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                api_key = auth[7:]
        if not api_key:
            api_key = request.query_params.get("token", "")
        client_ip = request.client.host if request.client else "unknown"
        if api_key:
            agent_id, role = resolve_api_key(api_key, self._registry)
            if agent_id:
                set_identity(agent_id, role, ip=client_ip)
                log.info("Auth: agent=%s role=%s ip=%s path=%s",
                         agent_id, role, client_ip, request.url.path)
        else:
            set_identity(None, "anonymous", ip=client_ip)
        try:
            response = await call_next(request)
            return response
        finally:
            clear_identity()
