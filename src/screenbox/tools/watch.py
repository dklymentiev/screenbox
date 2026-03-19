"""desktop_wait_stable and desktop_wait_change tools.

Smart watching modes powered by vision-stream.py WebSocket inside the container.
Uses vision-watch.py (baked into desktop container at /opt/screenbox/bin/) to
detect screen stabilization or changes without polling screenshots.
"""
import base64
import json
import logging
import time

from mcp.server.fastmcp import Image

logger = logging.getLogger(__name__)

# vision-watch.py is baked into desktop container via Dockerfile
_CONTAINER_SCRIPT = "/opt/screenbox/bin/vision-watch.py"
_RESULT_PATH = "/tmp/vision-watch-result.jpg"
_VISION_HOST = "127.0.0.1"  # vision-stream listens inside the container
_VISION_PORT = "8766"


def _run_watch(desktop, desktop_id, mode, region, timeout):
    """Run vision-watch.py inside container and return parsed result + image."""
    region_str = ",".join(str(v) for v in region)
    cmd = [
        "env", "DISPLAY=:99",
        "python3", _CONTAINER_SCRIPT,
        _VISION_HOST, _VISION_PORT, mode, region_str, str(timeout),
    ]

    # timeout +5s buffer for connection/cleanup
    raw = desktop._exec(cmd, timeout=int(timeout) + 10)

    # Parse output: log lines + last line is JSON result
    lines = raw.strip().split("\n")
    log_lines = []
    result = {}
    for line in lines:
        if line.startswith("{"):
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                log_lines.append(line)
        else:
            log_lines.append(line)

    # Try to read saved JPEG from container
    image_b64 = None
    try:
        jpg_data = desktop._exec_bytes(
            ["cat", _RESULT_PATH], timeout=5
        )
        if jpg_data and len(jpg_data) > 100:
            image_b64 = base64.b64encode(jpg_data).decode()
    except Exception:
        pass

    return result, log_lines, image_b64


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_wait_stable(
        desktop_id: str,
        region: str = "0,0,1920,1080",
        timeout: float = 5.0,
        intent: str = "",
        step: str = "",
    ) -> list:
        """Wait until screen region stops changing (stabilizes).

        Use after actions that trigger visual changes: clicking links, opening dialogs,
        loading pages. Returns when 3+ consecutive frames show no change.

        Much more efficient than polling with desktop_screenshot -- uses real-time
        vision-stream diff detection at 5fps inside the container.

        Args:
            desktop_id: Desktop to watch
            region: Watch region as "x1,y1,x2,y2" (default: full screen)
            timeout: Max seconds to wait (default: 5)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)

        parsed_region = [int(v.strip()) for v in region.split(",")]
        if len(parsed_region) != 4:
            return [json.dumps({"error": "Region must be x1,y1,x2,y2"})]

        result, log_lines, image_b64 = _run_watch(
            d, desktop_id, "stabilize", parsed_region, timeout
        )

        result["log"] = "\n".join(log_lines)
        log_action(desktop_id, "desktop_wait_stable",
                   {"region": region, "timeout": timeout},
                   result, t0, intent=intent, step=step)

        response = []
        if image_b64:
            response.append(Image(data=base64.b64decode(image_b64), format="jpeg"))
        response.append(json.dumps(result, indent=2))
        return response

    @mcp.tool()
    def desktop_wait_change(
        desktop_id: str,
        region: str = "0,0,1920,1080",
        timeout: float = 10.0,
        intent: str = "",
        step: str = "",
    ) -> list:
        """Wait until something changes in screen region.

        Use when waiting for: dialog appearance, notification popup, content update,
        animation start. Establishes 0.5s baseline then watches for any visual change.

        Much more efficient than polling with desktop_screenshot -- uses real-time
        vision-stream diff detection at 5fps inside the container.

        Args:
            desktop_id: Desktop to watch
            region: Watch region as "x1,y1,x2,y2" (default: full screen)
            timeout: Max seconds to wait (default: 10)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)

        parsed_region = [int(v.strip()) for v in region.split(",")]
        if len(parsed_region) != 4:
            return [json.dumps({"error": "Region must be x1,y1,x2,y2"})]

        result, log_lines, image_b64 = _run_watch(
            d, desktop_id, "change", parsed_region, timeout
        )

        result["log"] = "\n".join(log_lines)
        log_action(desktop_id, "desktop_wait_change",
                   {"region": region, "timeout": timeout},
                   result, t0, intent=intent, step=step)

        response = []
        if image_b64:
            response.append(Image(data=base64.b64decode(image_b64), format="jpeg"))
        response.append(json.dumps(result, indent=2))
        return response
