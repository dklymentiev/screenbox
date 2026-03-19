"""Screenbox Dashboard -- constants and environment configuration."""

import os
import re
from pathlib import Path

__version__ = "0.14.0"

_VALID_ID = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$')

PORT = int(os.environ.get("SCREENBOX_DASHBOARD_PORT", "16000"))
HOST = os.environ.get("SCREENBOX_DASHBOARD_HOST", "127.0.0.1")
VNC_BASE = os.environ.get("SCREENBOX_VNC_BASE", "")
DESKTOPS_DIR = os.environ.get("SCREENBOX_DESKTOPS_DIR", os.path.expanduser("~/.screenbox/desktops"))
# Host path for docker bind mounts (docker daemon sees host filesystem, not container's)
HOST_DESKTOPS_DIR = os.environ.get("SCREENBOX_HOST_DESKTOPS_DIR", DESKTOPS_DIR)
DISK_QUOTA_MB = int(os.environ.get("SCREENBOX_DISK_QUOTA_MB", "2048"))
API_TOKEN = os.environ.get("SCREENBOX_API_TOKEN", "")
# Dashboard auth mode: "token" = prompt for token (legacy), "none" = no auth,
# "auto" = auto-set session cookie for browser visitors (default)
DASHBOARD_AUTH = os.environ.get("SCREENBOX_DASHBOARD_AUTH", "auto")
CONFIG_FILE = os.path.join(DESKTOPS_DIR, ".dashboard-config.json")
DOCKER_NETWORK = os.environ.get("SCREENBOX_DOCKER_NETWORK", "bridge")
DESKTOP_IMAGE = os.environ.get("SCREENBOX_DESKTOP_IMAGE", "screenbox:latest")
SCREENSHOT_TTL = int(os.environ.get("SCREENBOX_SCREENSHOT_TTL", "5"))

MCP_API_URL = os.environ.get("SCREENBOX_MCP_API_URL", "http://screenbox-mcp:8080")
MCP_CONTAINER = os.environ.get("SCREENBOX_MCP_CONTAINER", "screenbox-screenbox-mcp-1")
# Removed: LOGS_DIR, MCP_LOGS_DIR, KNOWLEDGE_DIR, MCP_KNOWLEDGE_DIR
# Knowledge and logs are now served via MCP HTTP API (single source of truth)

# Infrastructure container name fragments to filter out from desktop listings
INFRA_FILTERS = ("dashboard", "mcp", "socket-proxy")

_MIME_MAP = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
}

_APP_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
