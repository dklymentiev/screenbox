"""Unit tests for Desktop and ExtensionClient."""

import json
import os
import socket
import struct
import threading
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from screenbox.desktop import Desktop, ExtensionClient
from screenbox.manager import DesktopManager, DesktopInfo, DesktopState

from tests.conftest import (
    free_port, ws_handshake_request, ws_encode_frame, ws_decode_frame,
)


# ---------------------------------------------------------------------------
# ExtensionClient: WebSocket frame encoding
# ---------------------------------------------------------------------------

class TestExtensionClientFrames:
    """Test WebSocket frame encoding/decoding logic."""

    def _start_echo_ws(self, port, response_fn=None):
        """Start a simple WS echo server that responds to commands."""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(1)

        def handler():
            import hashlib, base64
            client, _ = server.accept()
            # Read handshake
            data = b""
            while b"\r\n\r\n" not in data:
                data += client.recv(4096)
            # Extract key
            for line in data.decode().split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(
                hashlib.sha1((key + GUID).encode()).digest()
            ).decode()
            resp = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
            )
            client.sendall(resp.encode())

            # Read frames and respond
            while True:
                try:
                    b1 = client.recv(1)
                    if not b1:
                        break
                    b2 = client.recv(1)
                    length = b2[0] & 0x7F
                    masked = b2[0] & 0x80
                    if length == 126:
                        length = struct.unpack(">H", client.recv(2))[0]
                    elif length == 127:
                        length = struct.unpack(">Q", client.recv(8))[0]
                    if masked:
                        mask = client.recv(4)
                    payload = b""
                    while len(payload) < length:
                        payload += client.recv(length - len(payload))
                    if masked and mask:
                        payload = bytearray(payload)
                        for i in range(len(payload)):
                            payload[i] ^= mask[i % 4]
                        payload = bytes(payload)

                    msg = json.loads(payload)
                    if response_fn:
                        resp_data = response_fn(msg)
                    else:
                        resp_data = {"type": "response", "id": msg.get("id"), "result": {"ok": True}}

                    resp_bytes = json.dumps(resp_data).encode()
                    header = bytearray([0x81])
                    if len(resp_bytes) < 126:
                        header.append(len(resp_bytes))
                    elif len(resp_bytes) < 65536:
                        header.append(126)
                        header.extend(struct.pack(">H", len(resp_bytes)))
                    client.sendall(bytes(header) + resp_bytes)
                except Exception:
                    break
            client.close()
            server.close()

        t = threading.Thread(target=handler, daemon=True)
        t.start()
        return server

    def test_connect_and_command(self):
        port = free_port()
        self._start_echo_ws(port)
        time.sleep(0.1)

        client = ExtensionClient(port)
        result = client.send_command("ping", timeout=5)
        assert result["ok"] is True
        client.close()

    def test_command_with_params(self):
        port = free_port()

        def respond(msg):
            return {
                "type": "response",
                "id": msg["id"],
                "result": {"echo": msg.get("url", "none")},
            }

        self._start_echo_ws(port, respond)
        time.sleep(0.1)

        client = ExtensionClient(port)
        result = client.send_command("navigate", {"url": "https://test.com"}, timeout=5)
        assert result["echo"] == "https://test.com"
        client.close()

    def test_error_response(self):
        port = free_port()

        def respond(msg):
            return {"type": "error", "id": msg["id"], "error": "Test error"}

        self._start_echo_ws(port, respond)
        time.sleep(0.1)

        client = ExtensionClient(port)
        with pytest.raises(RuntimeError, match="Test error"):
            client.send_command("bad_cmd", timeout=5)
        client.close()

    def test_timeout(self):
        port = free_port()
        # Server that never responds
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(1)

        def handler():
            import hashlib, base64
            client, _ = server.accept()
            data = b""
            while b"\r\n\r\n" not in data:
                data += client.recv(4096)
            for line in data.decode().split("\r\n"):
                if line.lower().startswith("sec-websocket-key:"):
                    key = line.split(":", 1)[1].strip()
            GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
            accept = base64.b64encode(
                hashlib.sha1((key + GUID).encode()).digest()
            ).decode()
            client.sendall(
                f"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n".encode()
            )
            time.sleep(10)  # hang
            client.close()
            server.close()

        threading.Thread(target=handler, daemon=True).start()
        time.sleep(0.1)

        client = ExtensionClient(port)
        with pytest.raises(TimeoutError):
            client.send_command("test", timeout=1)
        client.close()

    def test_connection_refused(self):
        port = free_port()
        client = ExtensionClient(port)
        with pytest.raises(ConnectionRefusedError):
            client.send_command("test", timeout=2)

    def test_close(self):
        port = free_port()
        client = ExtensionClient(port)
        assert not client.connected
        client.close()
        assert not client.connected

    def test_msg_id_increments(self):
        client = ExtensionClient(99999)
        assert client._msg_id == 0
        # Can't send without connection, but ID tracking works
        client._msg_id = 5
        assert client._msg_id == 5


# ---------------------------------------------------------------------------
# Desktop: method tests with mocked manager
# ---------------------------------------------------------------------------

class TestDesktop:
    @pytest.fixture
    def mock_manager(self):
        mgr = MagicMock(spec=DesktopManager)
        mgr.get.return_value = DesktopInfo(
            desktop_id="test",
            state=DesktopState.RUNNING,
            ws_port=9999,
        )
        mgr._grid_state = {}
        return mgr

    @pytest.fixture
    def desktop(self, mock_manager):
        return Desktop(mock_manager, "test")

    def test_click(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.click(100, 200)
        calls = mock_manager.exec.call_args_list
        assert len(calls) >= 1
        # Click is now a single bash script with mousemove + click
        cmd = calls[-1][0][1]
        bash_script = cmd[-1] if isinstance(cmd, list) else str(cmd)
        assert "mousemove" in bash_script
        assert "100" in bash_script
        assert "200" in bash_script

    def test_double_click(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.double_click(50, 60)
        calls = mock_manager.exec.call_args_list
        assert len(calls) == 2

    def test_type_text(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.type_text("hello world")
        cmd = mock_manager.exec.call_args[0][1]
        assert "type" in cmd
        assert "hello world" in cmd

    def test_type_text_with_delay(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.type_text("abc", delay=50)
        cmd = mock_manager.exec.call_args[0][1]
        assert "--delay" in cmd
        assert "50" in cmd

    def test_key(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.key("ctrl+c")
        cmd = mock_manager.exec.call_args[0][1]
        assert "key" in cmd
        assert "ctrl+c" in cmd

    def test_scroll_down(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.scroll("down", 5)
        cmd = mock_manager.exec.call_args[0][1]
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "5" in cmd_str  # button 5 = scroll down

    def test_scroll_up(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.scroll("up", 3)
        cmd = mock_manager.exec.call_args[0][1]
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        assert "4" in cmd_str  # button 4 = scroll up

    def test_screenshot(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"\xff\xd8\xff\xe0fake_jpeg", stderr=b"",
        )
        data = desktop.screenshot()
        assert data == b"\xff\xd8\xff\xe0fake_jpeg"

    def test_screenshot_base64(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"JPEG_DATA", stderr=b"",
        )
        import base64
        result = desktop.screenshot_base64()
        assert result == base64.b64encode(b"JPEG_DATA").decode()

    def test_mouse_move(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.mouse_move(300, 400)
        cmd = mock_manager.exec.call_args[0][1]
        assert "300" in cmd
        assert "400" in cmd

    def test_right_click(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.right_click(10, 20)
        # Should call click with button=3
        calls = mock_manager.exec.call_args_list
        cmd = calls[-1][0][1]
        bash_script = cmd[-1] if isinstance(cmd, list) else str(cmd)
        assert "3" in bash_script

    def test_drag(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.drag(10, 20, 100, 200)
        calls = mock_manager.exec.call_args_list
        assert len(calls) >= 4  # moveto, mousedown, sleep, moveto, mouseup

    def test_hover(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"", stderr=b"",
        )
        desktop.hover(50, 60)
        cmd = mock_manager.exec.call_args[0][1]
        assert "50" in cmd
        assert "60" in cmd

    def test_get_clipboard(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"clipboard text", stderr=b"",
        )
        result = desktop.get_clipboard()
        assert result == "clipboard text"

    def test_shell(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0, stdout=b"file1\nfile2\n", stderr=b"",
        )
        result = desktop.shell("ls /tmp")
        assert result["exit_code"] == 0
        assert "file1" in result["stdout"]

    def test_shell_error(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=1, stdout=b"", stderr=b"not found",
        )
        result = desktop.shell("bad_command")
        assert result["exit_code"] == 1
        assert "not found" in result["stderr"]

    def test_get_cursor_position(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(
            returncode=0,
            stdout=b"X=100\nY=200\nSCREEN=0\nWINDOW=12345\n",
            stderr=b"",
        )
        result = desktop.get_cursor_position()
        assert result["X"] == 100
        assert result["Y"] == 200

    def test_get_windows_xdotool_fallback(self, desktop, mock_manager):
        # First call returns empty (wmctrl not found)
        # Then xdotool search, getwindowname, getwindowgeometry
        returns = [
            MagicMock(returncode=1, stdout=b"", stderr=b""),  # wmctrl
            MagicMock(returncode=0, stdout=b"12345\n67890\n", stderr=b""),  # search
            MagicMock(returncode=0, stdout=b"Chrome", stderr=b""),  # name 1
            MagicMock(returncode=0, stdout=b"X=0\nY=0\nWIDTH=1280\nHEIGHT=720\n", stderr=b""),
            MagicMock(returncode=0, stdout=b"Terminal", stderr=b""),  # name 2
            MagicMock(returncode=0, stdout=b"X=100\nY=100\nWIDTH=800\nHEIGHT=600\n", stderr=b""),
        ]
        mock_manager.exec.side_effect = returns
        windows = desktop.get_windows()
        assert len(windows) == 2
        assert windows[0]["name"] == "Chrome"

    def test_activate_window(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        desktop.activate_window("12345")
        cmd = mock_manager.exec.call_args[0][1]
        assert "windowactivate" in cmd
        assert "12345" in cmd

    def test_close_window_by_id(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        desktop.close_window("12345")
        cmd = mock_manager.exec.call_args[0][1]
        assert "windowclose" in cmd
        assert "12345" in cmd

    def test_close_window_active(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        desktop.close_window()
        cmd = mock_manager.exec.call_args[0][1]
        assert "alt+F4" in cmd

    def test_show_desktop(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        desktop.show_desktop()
        cmd = mock_manager.exec.call_args[0][1]
        assert "super+d" in cmd

    def test_navigate_extension_fallback(self, desktop, mock_manager):
        """Test that navigate falls back to xdotool when extension fails."""
        mock_manager.exec.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        # Extension will fail (no real WS server)
        result = desktop.navigate("https://test.com")
        assert result["method"] == "xdotool"
        assert result["url"] == "https://test.com"

    def test_semantics_error(self, desktop):
        """Semantics returns error dict when extension not available."""
        result = desktop.semantics()
        assert "error" in result

    def test_get_page_info_error(self, desktop):
        result = desktop.get_page_info()
        assert "error" in result

    def test_execute_js_error(self, desktop):
        result = desktop.execute_js("document.title")
        assert "error" in result

    def test_get_tabs_error(self, desktop):
        result = desktop.get_tabs()
        assert "error" in result

    def test_ext_click_error(self, desktop):
        result = desktop.ext_click(".button")
        assert "error" in result

    def test_ext_type_error(self, desktop):
        result = desktop.ext_type("input", "text")
        assert "error" in result

    def test_get_content_error(self, desktop):
        result = desktop.get_content()
        assert "error" in result

    def test_wait_for_selector(self, desktop):
        """wait_for with selector: tries extension, fails gracefully."""
        result = desktop.wait_for("selector:.test", timeout_ms=1000)
        assert result["found"] is False

    def test_wait_for_unknown_condition(self, desktop, mock_manager):
        mock_manager.exec.return_value = MagicMock(returncode=0, stdout=b"", stderr=b"")
        result = desktop.wait_for("badtype:value", timeout_ms=100)
        assert result["found"] is False
        assert "Unknown condition" in result.get("error", "")
