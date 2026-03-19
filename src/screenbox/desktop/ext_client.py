import base64
import json
import struct
import socket
import time
import os
from typing import Optional


class ExtensionClient:
    """WebSocket client to communicate with Chrome extension via ws-bridge.

    Connects to the ws-bridge running inside the container (port mapped to host).
    Sends commands and waits for responses.
    """

    def __init__(self, port: int, host: str = "127.0.0.1"):
        self.host = host
        self.port = port
        self._sock = None
        self._msg_id = 0

    def _connect(self):
        """Connect to ws-bridge as controller."""
        if self._sock:
            if self._is_alive():
                return
            # Stale socket -- close and reconnect
            self.close()

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        sock.connect((self.host, self.port))

        # WebSocket handshake
        key = base64.b64encode(os.urandom(16)).decode()
        handshake = (
            f"GET /controller HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        sock.sendall(handshake.encode())

        # Read response
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                raise ConnectionError("WS handshake failed")
            data += chunk

        if b"101" not in data:
            raise ConnectionError(f"WS handshake rejected: {data[:100]}")

        self._sock = sock

    def _send_frame(self, payload: str):
        """Send a masked WebSocket text frame."""
        data = payload.encode("utf-8")
        mask = os.urandom(4)

        header = bytearray()
        header.append(0x81)  # FIN + text opcode

        length = len(data)
        if length < 126:
            header.append(0x80 | length)  # masked
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))

        header.extend(mask)

        # Mask the payload
        masked = bytearray(data)
        for i in range(len(masked)):
            masked[i] ^= mask[i % 4]

        self._sock.sendall(bytes(header) + bytes(masked))

    def _recv_frame(self, timeout: float = 30) -> Optional[str]:
        """Receive a WebSocket text frame."""
        self._sock.settimeout(timeout)
        try:
            b1 = self._read_exact(1)
            b2 = self._read_exact(1)
            if not b1 or not b2:
                return None

            opcode = b1[0] & 0x0F
            masked = b2[0] & 0x80
            length = b2[0] & 0x7F

            if length == 126:
                raw = self._read_exact(2)
                length = struct.unpack(">H", raw)[0]
            elif length == 127:
                raw = self._read_exact(8)
                length = struct.unpack(">Q", raw)[0]

            if masked:
                mask = self._read_exact(4)
            else:
                mask = None

            payload = self._read_exact(length)

            if mask:
                payload = bytearray(payload)
                for i in range(len(payload)):
                    payload[i] ^= mask[i % 4]
                payload = bytes(payload)

            if opcode == 0x8:  # close
                return None
            if opcode == 0x9:  # ping - send pong
                pong = bytearray([0x8A, len(payload)]) + payload
                self._sock.sendall(bytes(pong))
                return self._recv_frame(timeout)

            return payload.decode("utf-8", errors="replace")
        except socket.timeout:
            return None

    def _read_exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self._sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def send_command(self, cmd_type: str, params: dict = None, timeout: float = 30) -> dict:
        """Send command to extension and wait for response."""
        self._connect()

        self._msg_id += 1
        msg = {"id": self._msg_id, "type": cmd_type}
        if params:
            msg.update(params)

        self._send_frame(json.dumps(msg))

        # Wait for response with matching id
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = deadline - time.time()
            raw = self._recv_frame(timeout=max(0.1, remaining))
            if raw is None:
                continue
            try:
                resp = json.loads(raw)
                if resp.get("id") == self._msg_id:
                    if resp.get("type") == "error":
                        raise RuntimeError(resp.get("error", "Extension error"))
                    return resp.get("result", resp)
            except json.JSONDecodeError:
                continue

        raise TimeoutError(f"Extension command '{cmd_type}' timed out after {timeout}s")

    def close(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _is_alive(self) -> bool:
        """Check if socket is still connected (non-blocking)."""
        if not self._sock:
            return False
        try:
            # getpeername() fails on dead sockets
            self._sock.getpeername()
            # Also check if socket has been closed by peer (readable with 0 bytes)
            self._sock.setblocking(False)
            try:
                data = self._sock.recv(1, socket.MSG_PEEK)
                if data == b'':
                    return False  # Peer closed
            except BlockingIOError:
                pass  # No data available -- socket is alive
            except (OSError, ConnectionError):
                return False
            finally:
                self._sock.setblocking(True)
            return True
        except (OSError, AttributeError):
            return False

    @property
    def connected(self) -> bool:
        return self._is_alive()
