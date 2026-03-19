"""Screenbox MCP Server entry point."""
import os
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
log = logging.getLogger("screenbox")


def _token_command(args: list[str]) -> None:
    """Handle: python -m screenbox token create/validate/list."""
    from .auth import TokenAuth

    auth = TokenAuth()

    if not args or args[0] == "help":
        print("Usage:")
        print("  python -m screenbox token create --agent AGENT_ID --desktops d1,d2 [--exp SECONDS]")
        print("  python -m screenbox token create --agent AGENT_ID --desktops '*'")
        print("  python -m screenbox token validate TOKEN")
        sys.exit(0)

    subcmd = args[0]

    if subcmd == "create":
        agent_id = None
        desktops = []
        exp_seconds = None
        i = 1
        while i < len(args):
            if args[i] == "--agent" and i + 1 < len(args):
                agent_id = args[i + 1]
                i += 2
            elif args[i] == "--desktops" and i + 1 < len(args):
                raw = args[i + 1]
                desktops = [d.strip() for d in raw.split(",") if d.strip()]
                i += 2
            elif args[i] == "--exp" and i + 1 < len(args):
                exp_seconds = int(args[i + 1])
                i += 2
            else:
                print(f"Unknown option: {args[i]}")
                sys.exit(1)

        if not agent_id:
            print("Error: --agent is required")
            sys.exit(1)
        if not desktops:
            print("Error: --desktops is required (comma-separated IDs or '*')")
            sys.exit(1)

        token = auth.create(agent_id, desktops, exp_seconds)
        print(token)

    elif subcmd == "validate":
        if len(args) < 2:
            print("Error: token string required")
            sys.exit(1)
        try:
            claims = auth.validate(args[1])
            import json
            print(json.dumps(claims, indent=2))
        except Exception as e:
            print(f"Invalid: {e}")
            sys.exit(1)

    else:
        print(f"Unknown token subcommand: {subcmd}")
        sys.exit(1)


def _run_dual(mcp, host: str, port: int):
    """Run both SSE and streamable-http transports on one server.

    Routes:
      /mcp            -> streamable-http (new clients)
      /sse            -> SSE event stream (legacy clients)
      /messages/      -> SSE message handler (legacy clients)
      /api/*          -> custom HTTP API routes (all clients)

    Uses streamable-http app as primary (has session lifecycle management),
    and mounts SSE transport routes alongside it.
    """
    import anyio
    import uvicorn
    from starlette.applications import Starlette

    mcp.settings.host = host
    mcp.settings.port = port

    # Primary: streamable-http app (manages its own session lifecycle)
    http_app = mcp.streamable_http_app()

    # SSE transport routes: /sse (GET stream) + /messages/ (POST handler)
    sse_app = mcp.sse_app()
    sse_routes = [r for r in sse_app.routes
                  if hasattr(r, 'path') and (r.path == '/sse' or r.path.startswith('/messages'))]

    # Combine: SSE routes first (specific paths), then HTTP app catches rest
    # HTTP app already includes /mcp + all custom routes (/api/*)
    all_routes = sse_routes + list(http_app.routes)

    from starlette.middleware import Middleware
    from .auth_middleware import AgentAuthMiddleware
    from .globals import registry as _registry

    combined = Starlette(
        routes=all_routes,
        debug=mcp.settings.debug,
        middleware=[Middleware(AgentAuthMiddleware, registry=_registry)],
        # Copy lifespan from http_app to preserve session manager lifecycle
        lifespan=http_app.router.lifespan_context if hasattr(http_app.router, 'lifespan_context') else None,
    )

    log.info("Starting MCP server (dual: streamable-http + sse) on %s:%d", host, port)
    log.info("  /mcp       -> streamable-http")
    log.info("  /sse       -> SSE (legacy)")
    log.info("  /api/*     -> HTTP API")

    async def _serve():
        config = uvicorn.Config(
            combined, host=host, port=port,
            log_level=mcp.settings.log_level.lower(),
        )
        server = uvicorn.Server(config)
        await server.serve()

    anyio.run(_serve)


def main():
    # Handle token subcommand before importing globals (avoids Docker dependency)
    if len(sys.argv) > 1 and sys.argv[1] == "token":
        _token_command(sys.argv[2:])
        return

    # Import globals to trigger tool registration
    from .globals import mcp

    transport = os.environ.get("SCREENBOX_TRANSPORT", "stdio")
    if "--sse" in sys.argv:
        transport = "sse"
    elif "--http" in sys.argv:
        transport = "streamable-http"
    elif "--both" in sys.argv:
        transport = "both"

    if transport in ("sse", "streamable-http", "both"):
        host = os.environ.get("SCREENBOX_HOST", "127.0.0.1")
        port = int(os.environ.get("SCREENBOX_PORT", "8080"))
        mcp.settings.host = host
        mcp.settings.port = port

    if transport == "both":
        _run_dual(mcp, host, port)
    else:
        if transport in ("sse", "streamable-http"):
            log.info("Starting MCP server (%s) on %s:%d",
                     transport, mcp.settings.host, mcp.settings.port)
        mcp.run(transport=transport)


if __name__ == "__main__":
    main()
