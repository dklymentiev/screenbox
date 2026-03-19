"""Unit tests for ws-bridge.py (WebSocket bridge inside container)."""

import hashlib
import base64
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import pytest

from tests.conftest import (
    free_port, ws_handshake_request, ws_encode_frame, ws_decode_frame,
)


WS_BRIDGE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "docker", "ws-bridge.py")


class TestWsBridge:
    """Tests for the WS bridge server (runs as subprocess)."""

    @pytest.fixture
    def bridge(self):
        """Start ws-bridge as a subprocess on a free port."""
        port = free_port()
        proc = subprocess.Popen(
            [sys.executable, WS_BRIDGE_PATH, str(port)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Wait for server to start
        for _ in range(20):
            try:
                s = socket.socket()
                s.connect(("127.0.0.1", port))
                s.close()
                break
            except ConnectionRefusedError:
                time.sleep(0.1)
        else:
            proc.kill()
            pytest.fail("ws-bridge did not start")

        yield port, proc

        proc.kill()
        proc.wait()

    def _ws_connect(self, port, path="/controller"):
        """Connect via WebSocket handshake."""
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect(("127.0.0.1", port))

        key = base64.b64encode(os.urandom(16)).decode()
        hs = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(hs.encode())

        data = b""
        while b"\r\n\r\n" not in data:
            data += sock.recv(4096)
        assert b"101" in data, f"Handshake failed: {data[:200]}"
        return sock

    def _send(self, sock, msg: dict):
        """Send JSON message as masked WS frame."""
        payload = json.dumps(msg)
        sock.sendall(ws_encode_frame(payload, masked=True))

    def _recv(self, sock, timeout=5) -> dict:
        """Receive JSON message from WS frame."""
        raw = ws_decode_frame(sock, timeout)
        return json.loads(raw)

    def test_controller_connects(self, bridge):
        port, proc = bridge
        sock = self._ws_connect(port, "/controller")
        sock.close()

    def test_extension_connects(self, bridge):
        port, proc = bridge
        sock = self._ws_connect(port, "/extension")
        sock.close()

    def test_controller_gets_error_when_no_extension(self, bridge):
        """Controller should get error if extension not connected."""
        port, proc = bridge
        ctrl = self._ws_connect(port, "/controller")
        self._send(ctrl, {"id": 1, "type": "test"})
        resp = self._recv(ctrl)
        assert resp["type"] == "error"
        assert "not connected" in resp["error"].lower()
        ctrl.close()

    def test_message_routing(self, bridge):
        """Controller -> bridge -> extension -> bridge -> controller."""
        port, proc = bridge

        # Connect extension first
        ext = self._ws_connect(port, "/extension")
        time.sleep(0.2)

        # Connect controller
        ctrl = self._ws_connect(port, "/controller")
        time.sleep(0.1)

        # Send command from controller
        self._send(ctrl, {"id": 42, "type": "ping"})

        # Extension receives command
        msg = self._recv(ext)
        assert msg["id"] == 42
        assert msg["type"] == "ping"

        # Extension sends response
        self._send(ext, {"type": "response", "id": 42, "result": {"pong": True}})

        # Controller receives response
        resp = self._recv(ctrl)
        assert resp["type"] == "response"
        assert resp["id"] == 42
        assert resp["result"]["pong"] is True

        ext.close()
        ctrl.close()

    def test_multiple_controllers(self, bridge):
        """Multiple controllers can connect; all receive extension responses."""
        port, proc = bridge

        ext = self._ws_connect(port, "/extension")
        time.sleep(0.2)

        ctrl1 = self._ws_connect(port, "/controller")
        ctrl2 = self._ws_connect(port, "/controller")
        time.sleep(0.1)

        # Send from ctrl1
        self._send(ctrl1, {"id": 1, "type": "test"})

        # Extension gets it
        msg = self._recv(ext)
        assert msg["id"] == 1

        # Extension responds
        self._send(ext, {"type": "response", "id": 1, "result": "ok"})

        # Both controllers receive
        r1 = self._recv(ctrl1)
        r2 = self._recv(ctrl2)
        assert r1["result"] == "ok"
        assert r2["result"] == "ok"

        ext.close()
        ctrl1.close()
        ctrl2.close()

    def test_extension_reconnect(self, bridge):
        """New extension connection replaces old one."""
        port, proc = bridge

        ext1 = self._ws_connect(port, "/extension")
        time.sleep(0.2)

        ext2 = self._ws_connect(port, "/extension")
        time.sleep(0.2)

        ctrl = self._ws_connect(port, "/controller")
        time.sleep(0.1)

        # Send command
        self._send(ctrl, {"id": 1, "type": "ping"})

        # New extension should get it
        msg = self._recv(ext2)
        assert msg["id"] == 1

        ext1.close()
        ext2.close()
        ctrl.close()

    def test_large_message(self, bridge):
        """Test messages > 125 bytes (2-byte length encoding)."""
        port, proc = bridge

        ext = self._ws_connect(port, "/extension")
        time.sleep(0.2)
        ctrl = self._ws_connect(port, "/controller")
        time.sleep(0.1)

        # Send large command
        big_data = "x" * 500
        self._send(ctrl, {"id": 1, "type": "test", "data": big_data})

        msg = self._recv(ext)
        assert msg["data"] == big_data
        assert len(msg["data"]) == 500

        ext.close()
        ctrl.close()
