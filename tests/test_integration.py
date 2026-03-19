"""Functional/integration tests for Screenbox.

These tests require Docker and a running screenbox container.
Mark: pytest -m integration

To run: ensure 'screenbox:latest' image is built, then:
    PYTHONPATH=src pytest tests/test_integration.py -v
"""

import base64
import json
import os
import socket
import struct
import time
import subprocess
import pytest

# Skip all if no Docker or image not built
pytestmark = pytest.mark.integration


def docker_available():
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def image_available():
    try:
        r = subprocess.run(
            ["docker", "images", "screenbox:latest", "--format", "{{.ID}}"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(r.stdout.strip())
    except Exception:
        return False


if not docker_available():
    pytest.skip("Docker not available", allow_module_level=True)
if not image_available():
    pytest.skip("screenbox:latest image not built", allow_module_level=True)


CONTAINER_NAME = "screenbox-pytest"
NOVNC_PORT = 17080
VNC_PORT = 17081
WS_PORT = 17082


@pytest.fixture(scope="module")
def container():
    """Start a screenbox container for the test session."""
    # Cleanup any previous test container
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME],
                   capture_output=True, timeout=10)

    # Start container
    result = subprocess.run([
        "docker", "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"127.0.0.1:{NOVNC_PORT}:6080",
        "-p", f"127.0.0.1:{VNC_PORT}:5900",
        "-p", f"127.0.0.1:{WS_PORT}:8765",
        "-e", "CHROME_URL=https://example.com",
        "--shm-size=256m",
        "screenbox:latest",
    ], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, f"Container start failed: {result.stderr}"

    # Wait for services
    _wait_for_port(WS_PORT, timeout=15)

    # Wait for Chrome + extension to connect to ws-bridge
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            logs = subprocess.run(
                ["docker", "logs", CONTAINER_NAME],
                capture_output=True, text=True, timeout=5,
            )
            if "Extension connected" in logs.stdout or "Extension connected" in logs.stderr:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        pytest.fail("Extension did not connect within 30s")

    time.sleep(2)  # Settle time after extension connect

    yield CONTAINER_NAME

    # Cleanup
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME],
                   capture_output=True, timeout=10)


def _wait_for_port(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.socket()
            s.settimeout(1)
            s.connect(("127.0.0.1", port))
            s.close()
            return True
        except (ConnectionRefusedError, socket.timeout):
            time.sleep(0.3)
    pytest.fail(f"Port {port} did not open within {timeout}s")


class WsClient:
    """Simple WS client for integration tests."""

    def __init__(self, port):
        self.sock = socket.socket()
        self.sock.settimeout(10)
        self.sock.connect(("127.0.0.1", port))

        key = base64.b64encode(os.urandom(16)).decode()
        hs = (
            f"GET /controller HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self.sock.sendall(hs.encode())
        data = b""
        while b"\r\n\r\n" not in data:
            data += self.sock.recv(4096)
        assert b"101" in data
        self._id = 0

    def command(self, cmd_type, params=None, timeout=15):
        self._id += 1
        msg = {"id": self._id, "type": cmd_type}
        if params:
            msg.update(params)
        self._send(json.dumps(msg))

        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self._recv(max(1, deadline - time.time()))
            if raw is None:
                continue
            resp = json.loads(raw)
            if resp.get("id") == self._id:
                if resp.get("type") == "error":
                    raise RuntimeError(resp.get("error", "unknown"))
                return resp.get("result", resp)
        raise TimeoutError(f"Command {cmd_type} timed out")

    def _send(self, payload):
        data = payload.encode()
        mask = os.urandom(4)
        header = bytearray([0x81])
        l = len(data)
        if l < 126:
            header.append(0x80 | l)
        elif l < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", l))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", l))
        header.extend(mask)
        masked = bytearray(data)
        for i in range(len(masked)):
            masked[i] ^= mask[i % 4]
        self.sock.sendall(bytes(header) + bytes(masked))

    def _recv(self, timeout=10):
        self.sock.settimeout(timeout)
        try:
            b1 = self.sock.recv(1)
            b2 = self.sock.recv(1)
            if not b1 or not b2:
                return None
            length = b2[0] & 0x7F
            masked = b2[0] & 0x80
            if length == 126:
                length = struct.unpack(">H", self.sock.recv(2))[0]
            elif length == 127:
                length = struct.unpack(">Q", self.sock.recv(8))[0]
            if masked:
                mask_bytes = self.sock.recv(4)
            else:
                mask_bytes = None
            payload = b""
            while len(payload) < length:
                payload += self.sock.recv(length - len(payload))
            if mask_bytes:
                payload = bytearray(payload)
                for i in range(len(payload)):
                    payload[i] ^= mask_bytes[i % 4]
                payload = bytes(payload)
            opcode = b1[0] & 0x0F
            if opcode == 0x8:
                return None
            return payload.decode("utf-8", errors="replace")
        except socket.timeout:
            return None

    def close(self):
        self.sock.close()


@pytest.fixture
def ws(container):
    """Create a WS client connected to the bridge."""
    client = WsClient(WS_PORT)
    yield client
    client.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestContainerServices:
    def test_novnc_port_open(self, container):
        """noVNC WebSocket proxy should be listening."""
        _wait_for_port(NOVNC_PORT, timeout=5)

    def test_vnc_port_open(self, container):
        """x11vnc should be listening."""
        _wait_for_port(VNC_PORT, timeout=5)

    def test_ws_bridge_port_open(self, container):
        """WS bridge should be listening."""
        _wait_for_port(WS_PORT, timeout=5)

    def test_chrome_running(self, container):
        """Chrome process should be running inside container."""
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "pgrep", "-f", "chrome"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
        assert result.stdout.strip()

    def test_display_server_running(self, container):
        """X display server should be running (Xvnc or Xvfb)."""
        for proc in ("Xvnc", "Xvfb"):
            result = subprocess.run(
                ["docker", "exec", CONTAINER_NAME, "pgrep", "-f", proc],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                break
        assert result.returncode == 0, "No X display server (Xvnc or Xvfb) found"


class TestExtensionCommands:
    def test_get_semantics(self, ws):
        result = ws.command("getSemantics", {"compact": True})
        assert "u" in result  # url
        assert "n" in result  # element count
        assert "e" in result  # elements array
        assert "v" in result  # viewport size

    def test_get_page_info(self, ws):
        result = ws.command("getPageInfo")
        assert "url" in result
        assert "title" in result

    def test_page_is_example_com(self, ws):
        result = ws.command("getPageInfo")
        assert "example.com" in result.get("url", "")

    def test_get_tabs(self, ws):
        result = ws.command("getTabs")
        assert isinstance(result, list)
        assert len(result) >= 1
        tab = result[0]
        assert "url" in tab
        assert "title" in tab
        assert "id" in tab

    def test_navigate(self, ws):
        result = ws.command("navigate", {"url": "https://httpbin.org/html"})
        assert result.get("success") is True

    def test_get_content_after_navigate(self, ws):
        ws.command("navigate", {"url": "https://httpbin.org/html"})
        time.sleep(2)
        result = ws.command("getContent")
        text = result.get("text", "")
        assert "Herman Melville" in text or "Moby" in text

    def test_get_window_bounds(self, ws):
        result = ws.command("getWindowBounds")
        assert "viewport" in result
        assert "window" in result
        assert result["viewport"]["width"] > 0
        assert result["viewport"]["height"] > 0

    def test_new_tab_and_close(self, ws):
        # Get initial tab count
        tabs_before = ws.command("getTabs")
        count_before = len(tabs_before)

        # Create new tab
        result = ws.command("newTab", {"url": "about:blank"})
        assert "tabId" in result

        # Verify count increased
        tabs_after = ws.command("getTabs")
        assert len(tabs_after) == count_before + 1

        # Close the new tab
        ws.command("closeTab", {"tabId": result["tabId"]})
        time.sleep(0.5)

        tabs_final = ws.command("getTabs")
        assert len(tabs_final) == count_before

    def test_switch_tab(self, ws):
        # Create second tab
        new = ws.command("newTab", {"url": "https://httpbin.org/html"})
        time.sleep(1)

        # Switch back to first tab
        tabs = ws.command("getTabs")
        first_tab = [t for t in tabs if not t["active"]][0]
        ws.command("switchTab", {"tabId": first_tab["id"]})

        # Verify
        tabs2 = ws.command("getTabs")
        active = [t for t in tabs2 if t["active"]][0]
        assert active["id"] == first_tab["id"]

        # Cleanup
        ws.command("closeTab", {"tabId": new["tabId"]})

    def test_ping(self, ws):
        result = ws.command("ping")
        assert result.get("pong") is True


class TestXdotoolCommands:
    """Test xdotool-based commands via docker exec."""

    def test_screenshot(self, container):
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "env", "DISPLAY=:99", "import", "-window", "root",
             "-quality", "50", "jpeg:-"],
            capture_output=True, timeout=10,
        )
        assert result.returncode == 0
        assert len(result.stdout) > 1000  # JPEG should be > 1KB
        assert result.stdout[:2] == b"\xff\xd8"  # JPEG magic

    def test_mouse_move(self, container):
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "env", "DISPLAY=:99", "xdotool",
             "mousemove", "640", "360"],
            capture_output=True, timeout=10,
        )
        assert result.returncode == 0

    def test_cursor_position(self, container):
        # Move to known position
        subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "env", "DISPLAY=:99", "xdotool",
             "mousemove", "--sync", "100", "200"],
            capture_output=True, timeout=5,
        )
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "env", "DISPLAY=:99", "xdotool",
             "getmouselocation", "--shell"],
            capture_output=True, text=True, timeout=5,
        )
        assert "X=100" in result.stdout
        assert "Y=200" in result.stdout

    def test_clipboard(self, container):
        # Set clipboard
        subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "bash", "-c",
             'echo -n "test clipboard" | DISPLAY=:99 xclip -selection clipboard'],
            capture_output=True, timeout=5,
        )
        # Get clipboard
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "env", "DISPLAY=:99", "xclip",
             "-selection", "clipboard", "-o"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.stdout == "test clipboard"

    def test_shell_exec(self, container):
        result = subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "bash", "-c", "echo hello && ls /home/screenbox/"],
            capture_output=True, text=True, timeout=5,
        )
        assert result.returncode == 0
        assert "hello" in result.stdout
        assert "extension" in result.stdout  # extension dir exists
