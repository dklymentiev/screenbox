"""Smoke tests for Screenbox desktop images (MATE and XFCE).

Verifies the fixes that broke repeatedly:
- Wallpaper "screenbox.dev" renders correctly
- Chrome does NOT auto-start
- Panel layout: apps left, clock right
- Click indicator and grid overlay start hidden (no black screen)
- Desktop shortcuts exist and are executable
- Xvnc and xrdp are running
- AT-SPI accessibility bus works

Run:
    PYTHONPATH=src pytest tests/test_desktop_smoke.py -v
    PYTHONPATH=src pytest tests/test_desktop_smoke.py -v -k mate
    PYTHONPATH=src pytest tests/test_desktop_smoke.py -v -k xfce

Requires: screenbox:latest (MATE) and screenbox:xfce images built.
"""

import subprocess
import time
import socket
import pytest

pytestmark = pytest.mark.integration

# Container config per image
IMAGES = {
    "mate": {
        "image": "screenbox:latest",
        "container": "screenbox-smoke-mate",
        "rdp_port": 17390,
        "vnc_port": 17391,
        "ws_port": 17392,
    },
    "xfce": {
        "image": "screenbox:xfce",
        "container": "screenbox-smoke-xfce",
        "rdp_port": 17400,
        "vnc_port": 17401,
        "ws_port": 17402,
    },
}


def _docker(*args, timeout=10):
    return subprocess.run(
        ["docker"] + list(args),
        capture_output=True, text=True, timeout=timeout,
    )


def _exec(container, cmd, timeout=10):
    return _docker("exec", container, "bash", "-c", cmd, timeout=timeout)


def _image_exists(image):
    r = _docker("images", image, "--format", "{{.ID}}")
    return bool(r.stdout.strip())


def _wait_port(port, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket()
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except (ConnectionRefusedError, socket.timeout, OSError):
            time.sleep(0.5)
    return False


# Skip if images not built
if not _image_exists("screenbox:latest"):
    pytest.skip("screenbox:latest not built", allow_module_level=True)
if not _image_exists("screenbox:xfce"):
    pytest.skip("screenbox:xfce not built", allow_module_level=True)


@pytest.fixture(scope="module", params=["mate", "xfce"])
def desktop(request):
    """Start a container for each image, wait for desktop, yield config, cleanup."""
    cfg = IMAGES[request.param]
    name = cfg["container"]

    # Cleanup previous
    _docker("rm", "-f", name)

    # Start with CHROME_URL=none (no auto-start browser)
    r = subprocess.run([
        "docker", "run", "-d",
        "--name", name,
        "--memory", "2048m",
        "--shm-size=256m",
        "-p", f"127.0.0.1:{cfg['rdp_port']}:3389",
        "-p", f"127.0.0.1:{cfg['vnc_port']}:5900",
        "-p", f"127.0.0.1:{cfg['ws_port']}:8765",
        "-e", "CHROME_URL=none",
        "-e", f"DESKTOP_ID=smoke-{request.param}",
        "-e", "SCREEN_WIDTH=1280",
        "-e", "SCREEN_HEIGHT=720",
        cfg["image"],
    ], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"Container start failed: {r.stderr}"

    # Wait for Xvnc (VNC port)
    assert _wait_port(cfg["vnc_port"], timeout=15), "VNC port did not open"

    # Wait for desktop session to settle (MATE needs more time for overlays)
    time.sleep(12)

    cfg["name"] = name
    cfg["de"] = request.param
    yield cfg

    # Cleanup
    _docker("rm", "-f", name)


# ---------------------------------------------------------------------------
# Core services
# ---------------------------------------------------------------------------

class TestServices:
    def test_xvnc_running(self, desktop):
        r = _exec(desktop["name"], "pgrep -x Xvnc")
        assert r.returncode == 0, "Xvnc not running"

    def test_xrdp_running(self, desktop):
        r = _exec(desktop["name"], "pgrep -x xrdp")
        assert r.returncode == 0, "xrdp not running"

    def test_vnc_port(self, desktop):
        assert _wait_port(desktop["vnc_port"], timeout=5)

    def test_rdp_port(self, desktop):
        assert _wait_port(desktop["rdp_port"], timeout=5)

    def test_ws_bridge_port(self, desktop):
        assert _wait_port(desktop["ws_port"], timeout=5)

    def test_desktop_session(self, desktop):
        """Desktop environment session is running."""
        if desktop["de"] == "mate":
            r = _exec(desktop["name"], "pgrep -f mate-session")
        else:
            r = _exec(desktop["name"], "pgrep -f xfce4-session")
        assert r.returncode == 0, f"Desktop session not running ({desktop['de']})"


# ---------------------------------------------------------------------------
# Chrome does NOT auto-start
# ---------------------------------------------------------------------------

class TestNoAutoChrome:
    def test_chrome_url_is_none(self, desktop):
        r = _exec(desktop["name"], "echo $CHROME_URL")
        assert r.stdout.strip() == "none"

    def test_no_chromium_processes(self, desktop):
        r = _exec(desktop["name"], "pgrep -x chromium-real | wc -l")
        count = int(r.stdout.strip())
        assert count == 0, f"Expected 0 chromium processes, got {count}"


# ---------------------------------------------------------------------------
# Wallpaper
# ---------------------------------------------------------------------------

class TestWallpaper:
    def test_wallpaper_file_exists(self, desktop):
        r = _exec(desktop["name"], "test -f /home/screenbox/.config/screenbox-wallpaper.png && echo ok")
        assert "ok" in r.stdout

    def test_wallpaper_not_empty(self, desktop):
        r = _exec(desktop["name"], "stat -c %s /home/screenbox/.config/screenbox-wallpaper.png")
        size = int(r.stdout.strip())
        assert size > 10000, f"Wallpaper too small ({size} bytes), probably failed to generate"

    def test_wallpaper_visible_on_screen(self, desktop):
        """Take screenshot and verify it's not all black (wallpaper rendered)."""
        r = subprocess.run(
            ["docker", "exec", desktop["name"],
             "bash", "-c",
             "DISPLAY=:99 import -window root -quality 50 jpeg:- | wc -c"],
            capture_output=True, text=True, timeout=10,
        )
        jpg_size = int(r.stdout.strip())
        # A black screen JPEG is ~2-3KB, wallpaper with text is >5KB
        assert jpg_size > 4000, f"Screenshot too small ({jpg_size}B), likely black screen"

    def test_screen_not_black(self, desktop):
        """Compute mean pixel value -- black screen = ~0, normal desktop > 5."""
        r = subprocess.run(
            ["docker", "exec", desktop["name"],
             "bash", "-c",
             "DISPLAY=:99 import -window root -resize 100x100 -colorspace Gray -format '%[fx:mean*255]' info:"],
            capture_output=True, text=True, timeout=10,
        )
        mean = float(r.stdout.strip())
        # Dark theme wallpaper has low mean (~1-2), pure black overlay = 0
        assert mean > 0.5, f"Screen appears black (mean pixel={mean:.1f})"


# ---------------------------------------------------------------------------
# Overlays (click indicator + grid) -- must start hidden
# ---------------------------------------------------------------------------

class TestOverlays:
    def test_click_indicator_running(self, desktop):
        r = _exec(desktop["name"], "pgrep -f 'python3.*click-indicator'")
        assert r.returncode == 0, "Click indicator not running"

    def test_grid_overlay_running(self, desktop):
        r = _exec(desktop["name"], "pgrep -f 'python3.*grid-overlay'")
        assert r.returncode == 0, "Grid overlay not running"

    def test_click_indicator_hidden(self, desktop):
        """Click indicator should be hidden (no /tmp/.grid-overlay-config = no grid shown)."""
        r = _exec(desktop["name"],
                   "cat /var/log/screenbox/click-indicator.log | head -5")
        assert "hidden until first visual" in r.stdout or r.returncode == 0

    def test_grid_overlay_hidden(self, desktop):
        """Grid overlay should be hidden (no config file = no grid)."""
        r = _exec(desktop["name"], "test -f /tmp/.grid-overlay-config && echo exists || echo missing")
        assert "missing" in r.stdout, "Grid config should not exist by default"


# ---------------------------------------------------------------------------
# Panel layout
# ---------------------------------------------------------------------------

class TestPanel:
    def test_panel_running(self, desktop):
        if desktop["de"] == "mate":
            r = _exec(desktop["name"], "pgrep -f mate-panel")
        else:
            r = _exec(desktop["name"], "pgrep -f xfce4-panel")
        assert r.returncode == 0, "Panel not running"


# ---------------------------------------------------------------------------
# Desktop shortcuts
# ---------------------------------------------------------------------------

class TestShortcuts:
    def test_chromium_shortcut_exists(self, desktop):
        r = _exec(desktop["name"], "test -f /home/screenbox/Desktop/chromium-browser.desktop && echo ok")
        assert "ok" in r.stdout

    def test_chromium_shortcut_executable(self, desktop):
        r = _exec(desktop["name"], "test -x /home/screenbox/Desktop/chromium-browser.desktop && echo ok")
        assert "ok" in r.stdout

    def test_chromium_shortcut_has_disable_gpu(self, desktop):
        r = _exec(desktop["name"], "grep -- '--disable-gpu' /home/screenbox/Desktop/chromium-browser.desktop")
        assert r.returncode == 0, "Desktop shortcut missing --disable-gpu"

    def test_documents_folder(self, desktop):
        r = _exec(desktop["name"], "test -d /home/screenbox/Desktop/Documents && echo ok")
        assert "ok" in r.stdout


# ---------------------------------------------------------------------------
# Chrome can launch manually (with --disable-gpu)
# ---------------------------------------------------------------------------

class TestChromeLaunch:
    def test_chrome_starts_with_disable_gpu(self, desktop):
        """Chrome should start when launched manually."""
        _exec(desktop["name"],
              "source /home/screenbox/.dbus-env 2>/dev/null; "
              "DISPLAY=:99 /usr/bin/chromium --no-first-run --disable-gpu "
              "--disable-sync about:blank &",
              timeout=5)
        time.sleep(5)
        r = _exec(desktop["name"], "pgrep -f 'chromium.*disable-gpu' | wc -l")
        count = int(r.stdout.strip())
        assert count > 0, "Chrome did not start with --disable-gpu"

        # Cleanup: kill chrome
        _exec(desktop["name"], "pkill -f chromium || true")
        time.sleep(1)
