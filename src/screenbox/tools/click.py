"""desktop_click tool."""
import base64 as b64mod
import json
import logging
import time

from mcp.server.fastmcp import Image

from screenbox.tools.watch import _run_watch

_log = logging.getLogger("screenbox.click")


def _observe_instant(d, desktop_id, x, y, ocr_radius, result):
    """Instant observe: screenshot + OCR around click point."""
    radius = d._adaptive_radius(ocr_radius)
    _log.info(f"observe instant: radius={radius}")
    ocr_result = d._cursor_ocr({"x": x, "y": y}, radius=radius)
    result["elements"] = ocr_result.get("elements", [])
    result["count"] = ocr_result.get("count", 0)
    result["radius"] = radius
    image_b64 = ocr_result.pop("image_base64", None)
    return image_b64


def _observe_stable(d, desktop_id, x, y, ocr_radius, result, timeout=3.0):
    """Wait for stabilization around click point, then screenshot + OCR.

    Uses vision-stream diff detection at 5fps -- waits until the region
    around the click point stops changing, then captures the final state.
    Much better than instant observe for clicks that trigger animations,
    checkmarks, page transitions, etc.
    """
    radius = d._adaptive_radius(ocr_radius)
    _log.info(f"observe stable: radius={radius}, timeout={timeout}s")

    # Region around click point (clamped to screen)
    x1 = max(0, x - radius)
    y1 = max(0, y - radius)
    x2 = min(1920, x + radius)
    y2 = min(1080, y + radius)
    region = [x1, y1, x2, y2]

    # Wait for stabilization via vision-stream
    watch_result, watch_log, _watch_image = _run_watch(
        d, desktop_id, "stabilize", region, timeout
    )
    result["stabilized"] = watch_result.get("stabilized", False)
    result["wait_elapsed"] = watch_result.get("elapsed", 0)
    _log.info(f"observe stable: stabilized={result['stabilized']} "
              f"in {result['wait_elapsed']}s")

    # Now do OCR on the stabilized screen
    ocr_result = d._cursor_ocr({"x": x, "y": y}, radius=radius)
    result["elements"] = ocr_result.get("elements", [])
    result["count"] = ocr_result.get("count", 0)
    result["radius"] = radius
    image_b64 = ocr_result.pop("image_base64", None)
    return image_b64


def _build_tips(desktop_id, elements):
    """Build click target tips from OCR elements."""
    tips = []
    for el in sorted(elements, key=lambda e: e.get("conf", 0), reverse=True):
        if el.get("conf", 0) >= 50 and el.get("text", "").strip():
            tips.append(
                f'desktop_click("{desktop_id}", {el["cx"]}, {el["cy"]})  # {el["text"]}'
            )
    return tips[:6]


def register(mcp, get_desktop, get_manager, log_action, app_catalog):

    @mcp.tool()
    def desktop_click(desktop_id: str, x: int, y: int,
                      button: int = 1, clicks: int = 1,
                      observe: bool = True,
                      wait: bool = False,
                      ocr_radius: int = 0,
                      intent: str = "",
                      step: str = "") -> list:
        """Click at screen coordinates.

        Args:
            desktop_id: Desktop to click on
            x: X coordinate
            y: Y coordinate
            button: Mouse button (1=left, 2=middle, 3=right)
            clicks: Number of clicks (1=single, 2=double)
            observe: Return image + OCR around click point (eliminates need for separate screenshot)
            wait: Wait for screen to stabilize before capturing (use for clicks that trigger animations/transitions). Implies observe=true.
            ocr_radius: Override OCR radius in pixels (0 = auto ~22% of screen)
        """
        t0 = time.time()
        d = get_desktop(desktop_id)
        if clicks >= 2:
            d.double_click(x, y)
            result = {"clicked": True, "x": x, "y": y, "double": True}
        else:
            d.click(x, y, button)
            result = {"clicked": True, "x": x, "y": y, "button": button}

        # Determine observe mode
        if wait:
            observe_mode = "stable"
        elif observe:
            observe_mode = "instant"
        else:
            observe_mode = None

        if observe_mode:
            try:
                if observe_mode == "stable":
                    image_b64 = _observe_stable(
                        d, desktop_id, x, y, ocr_radius, result)
                else:
                    image_b64 = _observe_instant(
                        d, desktop_id, x, y, ocr_radius, result)

                tips = _build_tips(desktop_id, result.get("elements", []))
                if tips:
                    result["tip"] = "Nearby click targets:\n" + "\n".join(tips)

                log_action(desktop_id, "desktop_click",
                           {"x": x, "y": y, "button": button,
                            "observe": observe_mode},
                           {"elements_count": result.get("count", 0)}, t0,
                           intent=intent, step=step)

                response = []
                if image_b64:
                    response.append(Image(
                        data=b64mod.b64decode(image_b64), format="jpeg"))
                response.append(json.dumps(result, indent=2))
                return response
            except Exception as e:
                _log.exception(f"observe ({observe_mode}) failed: {e}")
                result["observe_error"] = str(e)
                return [json.dumps(result)]

        log_action(desktop_id, "desktop_click",
                   {"x": x, "y": y, "button": button, "clicks": clicks},
                   result, t0, intent=intent, step=step)
        return [json.dumps(result)]
