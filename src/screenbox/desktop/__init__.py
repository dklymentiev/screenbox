"""Desktop interaction via docker exec.

OS-level interaction: xdotool via docker exec (click, type, key, scroll, screenshot).
Browser interaction is delegated to BrowserClient (see browser.py).

The extension runs inside the container and connects to a WS bridge.
The MCP server connects to the WS bridge's mapped port from the host.
"""

import base64
import logging
import re
import time
from typing import Optional

from ..logger import ActionLogger
from ..manager import DesktopManager

from .ext_client import ExtensionClient
from .input import InputMixin
from .ocr import OCRMixin
from .grid import GridMixin
from .a11y import AccessibilityMixin
from .browser_proxy import BrowserProxyMixin
from .find import FindMixin

log = logging.getLogger("screenbox.desktop")

DISPLAY = ":99"
OCR_RADIUS = 200  # legacy default for non-cell methods


class Desktop(
    InputMixin,
    OCRMixin,
    GridMixin,
    AccessibilityMixin,
    BrowserProxyMixin,
    FindMixin,
):
    """Desktop interaction for a specific container.

    Uses Chrome extension (via WS bridge) as primary interaction method.
    Falls back to xdotool via docker exec for low-level input.
    """

    def __init__(self, manager: DesktopManager, desktop_id: str,
                 action_logger: Optional[ActionLogger] = None,
                 agent_id: Optional[str] = None):
        self.manager = manager
        self.desktop_id = desktop_id
        self._ext_client: Optional[ExtensionClient] = None
        self._logger = action_logger
        self._agent_id = agent_id
        self._browser = None
        # Unified grid state: persisted in manager, survives Desktop re-creation
        saved = manager._grid_state.get(desktop_id)
        self._grid_cols: Optional[int] = saved[0] if saved else None
        self._grid_rows: Optional[int] = saved[1] if saved else None

    @property
    def browser(self) -> "BrowserClient":
        """Lazy-initialized BrowserClient for all browser interaction."""
        if self._browser is None:
            from ..browser import BrowserClient
            self._browser = BrowserClient(self)
        return self._browser

    def _log(self, tool: str, args: dict, result=None, error: str = None,
             start_time: float = 0):
        """Log an action if logger is configured. Includes sub-timings."""
        if not self._logger:
            return
        duration = int((time.time() - start_time) * 1000) if start_time else None
        extra = {}
        if self._last_exec_ms:
            extra["exec_ms"] = self._last_exec_ms
            self._last_exec_ms = 0
        if self._last_ws_ms:
            extra["ws_ms"] = self._last_ws_ms
            self._last_ws_ms = 0
        self._logger.log(tool, args, result=result, error=error,
                         duration_ms=duration, agent_id=self._agent_id, **extra)

    # -- Internal execution with timing --

    _last_exec_ms: int = 0
    _last_ws_ms: int = 0

    def _get_ext(self) -> ExtensionClient:
        """Get or create extension client. Retries on connection failure."""
        if self._ext_client is None:
            info = self.manager.get(self.desktop_id)
            # When running in Docker network, connect directly to container:8765
            if self.manager.config.docker_network:
                from ..manager import CONTAINER_PREFIX
                host = f"{CONTAINER_PREFIX}{self.desktop_id}"
                self._ext_client = ExtensionClient(port=8765, host=host)
            elif info and info.ws_port:
                self._ext_client = ExtensionClient(info.ws_port)
            else:
                raise RuntimeError("No WS port available for extension")
        return self._ext_client

    def _ext_cmd(self, command: str, params: dict = None, timeout: int = 10) -> dict:
        """Send extension command with auto-reconnect on failure."""
        try:
            ext = self._get_ext()
            log.debug("ext_cmd '%s' -> %s:%s (connected=%s)",
                      command, ext.host, ext.port, ext.connected)
            return ext.send_command(command, params or {}, timeout=timeout)
        except Exception as e:
            log.warning("Extension command '%s' failed on %s:%s: %s, retrying...",
                        command,
                        self._ext_client.host if self._ext_client else "?",
                        self._ext_client.port if self._ext_client else "?", e)
            if self._ext_client:
                self._ext_client.close()
                self._ext_client = None
            time.sleep(0.5)
            try:
                ext = self._get_ext()
                log.info("ext_cmd '%s' retry -> %s:%s", command, ext.host, ext.port)
                return ext.send_command(command, params or {}, timeout=timeout)
            except Exception as e2:
                log.error("Extension command '%s' retry failed on %s:%s: %s",
                          command,
                          self._ext_client.host if self._ext_client else "?",
                          self._ext_client.port if self._ext_client else "?", e2)
                # Check if browser is running to give a better error
                if "Extension not connected" in str(e2) or "Extension not connected" in str(e):
                    try:
                        result = self._exec(
                            ["sh", "-c", "pgrep -x chromium || pgrep -x chrome"],
                            timeout=3)
                        if not result.strip():
                            raise RuntimeError(
                                "Browser is not running. "
                                "Launch it: desktop_manage(action='app_launch', app='chrome'). "
                                "Chrome will start with Screenbox extension automatically."
                            ) from e2
                    except RuntimeError:
                        raise
                    except Exception:
                        pass  # pgrep check failed, raise original error
                raise

    def _exec(self, cmd: list[str], timeout: int = 10) -> str:
        """Run command in container, return stdout as string."""
        t = time.time()
        result = self.manager.exec(self.desktop_id, cmd, timeout=timeout)
        self._last_exec_ms = int((time.time() - t) * 1000)
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
            log.warning("Command failed in %s: %s -> %s", self.desktop_id, cmd[:3], stderr[:200])
        return result.stdout.decode("utf-8", errors="replace") if result.stdout else ""

    def _exec_bytes(self, cmd: list[str], timeout: int = 10) -> bytes:
        """Run command in container, return raw stdout bytes."""
        t = time.time()
        result = self.manager.exec(self.desktop_id, cmd, timeout=timeout)
        self._last_exec_ms = int((time.time() - t) * 1000)
        if result.returncode != 0 or not result.stdout:
            stderr = result.stderr.decode("utf-8", errors="replace")[:200] if result.stderr else ""
            if stderr:
                log.warning("exec_bytes empty in %s: rc=%d cmd=%s stderr=%s",
                            self.desktop_id, result.returncode, cmd[:3], stderr)
        return result.stdout if result.stdout else b""

    def _xdotool(self, *args: str) -> str:
        """Run xdotool command."""
        return self._exec(["env", "DISPLAY=" + DISPLAY, "xdotool"] + list(args))

    # -- Screenshot --

    def _screenshot_raw(self, quality: int = 75) -> bytes:
        """Capture raw screen (including any overlays) as JPEG bytes."""
        data = self._exec_bytes([
            "env", "DISPLAY=" + DISPLAY,
            "import", "-window", "root",
            "-quality", str(quality),
            "jpeg:-",
        ], timeout=15)
        if not data:
            log.warning("_screenshot_raw empty for %s (DISPLAY=%s)",
                        self.desktop_id, DISPLAY)
        return data

    def screenshot(self, quality: int = 75, _log: bool = True) -> bytes:
        """Capture clean desktop as JPEG (grid overlay auto-hidden if active).

        If dashboard grid is on, suspends it via SIGUSR1, captures, resumes via SIGUSR2.
        This is the default -- always returns a clean image without debug overlays.
        """
        t0 = time.time()
        grid_pid = None
        try:
            pid_out = self._exec(["cat", "/tmp/.grid-overlay-pid"], timeout=2)
            pid_str = pid_out.strip()
            # Validate PID is digits only to prevent injection
            if pid_str and pid_str.isdigit():
                grid_pid = int(pid_str)
        except Exception:
            pass
        if grid_pid:
            gp = str(grid_pid)
            # Check if grid overlay process is alive; clean up stale PID file if not
            try:
                alive = self._exec(["bash", "-c", "kill -0 $1 2>/dev/null && echo y || echo n", "bash", gp], timeout=2).strip()
            except Exception:
                alive = "n"
            if alive == "y":
                data = self._exec_bytes(["bash", "-c",
                    "kill -USR1 $1 && sleep 0.1 && "
                    "DISPLAY=:99 import -window root -quality $2 jpg:- ; "
                    "kill -USR2 $1",
                    "bash", gp, str(int(quality))],
                    timeout=15)
            else:
                # Stale PID file — remove it and take normal screenshot
                try:
                    self._exec(["rm", "-f", "/tmp/.grid-overlay-pid"], timeout=2)
                except Exception:
                    pass
                data = self._screenshot_raw(quality=quality)
        else:
            data = self._screenshot_raw(quality=quality)
        if _log:
            self._log("screenshot", {"quality": quality},
                      result=f"[{len(data)} bytes]" if data else None,
                      error="empty screenshot" if not data else None, start_time=t0)
        return data

    def screenshot_base64(self, quality: int = 75) -> str:
        """Capture screen as base64-encoded JPEG."""
        data = self.screenshot(quality=quality)
        if not data:
            return ""
        return base64.b64encode(data).decode("ascii")

    # -- Screen geometry --

    def _get_screen_size(self) -> tuple[int, int]:
        """Get screen dimensions via xdpyinfo. Returns (width, height)."""
        try:
            raw = self._exec(["env", f"DISPLAY={DISPLAY}", "xdpyinfo"], timeout=3)
            for line in raw.split("\n"):
                if "dimensions:" in line:
                    parts = line.split("dimensions:")[1].strip().split()[0]
                    w, h = map(int, parts.split("x"))
                    return w, h
        except Exception:
            pass
        return 1280, 720

    def _adaptive_radius(self, ocr_radius: int = 0) -> int:
        """Calculate look radius adaptive to screen resolution.

        Formula: radius = min(screen_w, screen_h) * look_factor
        Default factor 0.2 gives ~22% width coverage across resolutions.
        """
        if ocr_radius > 0:
            return ocr_radius
        w, h = self._get_screen_size()
        return int(min(w, h) * self.manager.config.look_factor)

    # -- Shell --

    def shell(self, command: str, timeout: int = 30) -> dict:
        """Execute shell command inside the container."""
        t0 = time.time()
        result = self.manager.exec(
            self.desktop_id,
            ["env", f"DISPLAY={DISPLAY}", "bash", "-c", command],
            timeout=timeout,
        )
        stdout = result.stdout.decode("utf-8", errors="replace") if result.stdout else ""
        stderr = result.stderr.decode("utf-8", errors="replace") if result.stderr else ""
        out = {
            "exit_code": result.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }
        self._log("shell", {"command": command[:200]},
                  result={"exit_code": result.returncode},
                  error=stderr[:200] if result.returncode != 0 else None,
                  start_time=t0)
        return out

    # -- Recording --

    def record_start(self, fps: int = 10, max_duration: int = 1800) -> dict:
        """Start screen recording using ffmpeg x11grab.

        Args:
            fps: Frames per second (default 10, good balance of quality/size).
            max_duration: Max recording length in seconds (default 1800 = 30min).

        Returns:
            {"recording": True, "file": filename} or {"error": ...}
        """
        # Check if already recording
        check = self.manager.exec(
            self.desktop_id,
            ["bash", "-c", "pgrep -x ffmpeg || true"],
            timeout=5,
        )
        if check.stdout and check.stdout.decode().strip():
            return {"error": "Already recording", "pid": check.stdout.decode().strip()}

        # Get screen resolution and check if recording is feasible
        w, h = self._get_screen_size()
        max_rec_pixels = 1920 * 1080  # ~2MP -- FFmpeg x11grab can't encode larger in realtime
        if w * h > max_rec_pixels:
            return {
                "error": f"Screen too large for recording: {w}x{h}. "
                f"FFmpeg x11grab cannot encode >{max_rec_pixels // 1000}K pixels in realtime. "
                f"Max supported: 1920x1080. Recreate desktop with smaller resolution to record."
            }
        from datetime import datetime
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{ts}.mp4"
        # Use shared volume path if set, fallback to old /recordings bind mount
        rec_dir = f"/data/screenbox/desktops/{self.desktop_id}/recordings"

        # Validate and clamp user-controlled parameters
        fps = max(1, min(int(fps), 60))
        max_duration = max(1, min(int(max_duration), 3600))

        # Start ffmpeg in background (setsid to survive docker exec exit)
        result = self.manager.exec(
            self.desktop_id,
            ["bash", "-c",
             'mkdir -p "$1" && '
             'DISPLAY=:99 setsid ffmpeg -y '
             '-f x11grab -video_size "${2}x${3}" -r "$4" -i :99 '
             '-c:v libx264 -preset ultrafast -crf 28 -pix_fmt yuv420p '
             '-t "$5" "$1/$6" '
             '< /dev/null > /tmp/ffmpeg.log 2>&1 &',
             "bash", rec_dir, str(w), str(h), str(fps),
             str(max_duration), filename],
            timeout=5,
        )
        self._log("record_start", {"fps": fps, "file": filename},
                  result={"started": True})
        return {"recording": True, "file": filename}

    def record_stop(self) -> dict:
        """Stop active screen recording.

        Sends SIGINT to ffmpeg for clean MP4 finalization.

        Returns:
            {"stopped": True, "file": filename} or {"error": ...}
        """
        # Find ffmpeg pid
        check = self.manager.exec(
            self.desktop_id,
            ["bash", "-c", "pgrep -x ffmpeg || true"],
            timeout=5,
        )
        pid = check.stdout.decode().strip() if check.stdout else ""
        if not pid:
            return {"error": "No active recording"}

        pid_num = pid.split()[0]
        if not pid_num.isdigit():
            return {"error": "Invalid ffmpeg PID"}

        # Find the output file from ffmpeg cmdline
        cmd_check = self.manager.exec(
            self.desktop_id,
            ["bash", "-c", "cat /proc/$1/cmdline 2>/dev/null | tr '\\0' ' ' || true",
             "bash", pid_num],
            timeout=5,
        )
        cmdline = cmd_check.stdout.decode() if cmd_check.stdout else ""

        # Send SIGINT for clean close
        self.manager.exec(
            self.desktop_id,
            ["bash", "-c", "kill -INT $1", "bash", pid_num],
            timeout=5,
        )
        # Wait for ffmpeg to finalize
        import time as _time
        _time.sleep(2)

        # Extract filename from cmdline
        filename = ""
        if ".mp4" in cmdline:
            # Find the mp4 path in cmdline (works for both old /recordings/ and new /data/screenbox/...)
            mp4_match = re.search(r'(/[^\s\x00]+\.mp4)', cmdline)
            if mp4_match:
                filename = mp4_match.group(1).rsplit("/", 1)[-1]

        self._log("record_stop", {}, result={"stopped": True, "file": filename})
        return {"stopped": True, "file": filename}

    def record_status(self) -> dict:
        """Check if recording is active."""
        check = self.manager.exec(
            self.desktop_id,
            ["bash", "-c", "pgrep -x ffmpeg || true"],
            timeout=5,
        )
        pid = check.stdout.decode().strip() if check.stdout else ""
        return {"recording": bool(pid), "pid": pid or None}

    # -- Cursor --

    def get_cursor_position(self) -> dict:
        """Get current cursor position."""
        out = self._xdotool("getmouselocation", "--shell")
        pos = {}
        for line in out.strip().split("\n"):
            if "=" in line:
                k, v = line.split("=", 1)
                pos[k.strip()] = int(v.strip()) if v.strip().isdigit() else v.strip()
        return pos

    def get_mouse_pos(self) -> tuple:
        """Get current mouse position."""
        out = self._exec(["env", f"DISPLAY={DISPLAY}", "xdotool", "getmouselocation"])
        # output: "x:123 y:456 screen:0 window:789"
        parts = dict(p.split(":") for p in out.strip().split() if ":" in p)
        return int(parts.get("x", 0)), int(parts.get("y", 0))
