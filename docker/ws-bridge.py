#!/usr/bin/env python3
"""WebSocket bridge for Chrome extension communication.

Sits between the MCP server (external client) and the Chrome extension.
Extension connects as 'extension' client, MCP server connects as 'controller'.
Commands from controller are forwarded to extension, responses back.

Runs inside the Docker container.
"""

import asyncio
import json
import logging
import sys

logging.basicConfig(level=logging.INFO, format="[ws-bridge] %(message)s")
log = logging.getLogger("ws-bridge")

# We need websockets library - use the simple server from python3-websocket
# But python3-websocket doesn't have async server. Use asyncio + raw sockets instead.
# Actually, let's use the built-in http.server approach with a simple WebSocket impl.
# For simplicity, use a synchronous approach with threading.

import hashlib
import hmac
import base64
import os
import struct
import socket
import threading
import select
from urllib.parse import urlparse, parse_qs

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
# Authentication token: if set, clients must include ?token=<value> in WebSocket URL
WS_TOKEN = os.environ.get("SCREENBOX_WS_TOKEN", "")
log.info("WS_TOKEN configured: %s (len=%d)", "yes" if WS_TOKEN else "no", len(WS_TOKEN))


class WebSocketConnection:
    """Minimal WebSocket connection handler."""

    def __init__(self, sock, addr, role=None):
        self.sock = sock
        self.addr = addr
        self.role = role  # 'extension' or 'controller'
        self.closed = False

    def send(self, data):
        if self.closed:
            return
        if isinstance(data, str):
            data = data.encode('utf-8')
            opcode = 0x1  # text
        else:
            opcode = 0x2  # binary

        length = len(data)
        header = bytearray()
        header.append(0x80 | opcode)  # FIN + opcode

        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack('>H', length))
        else:
            header.append(127)
            header.extend(struct.pack('>Q', length))

        try:
            self.sock.sendall(bytes(header) + data)
        except Exception:
            self.closed = True

    def recv(self):
        """Read one WebSocket frame. Returns (opcode, data) or None on close."""
        try:
            b1 = self._read_exact(1)
            if not b1:
                return None
            b2 = self._read_exact(1)
            if not b2:
                return None

            opcode = b1[0] & 0x0F
            masked = b2[0] & 0x80
            length = b2[0] & 0x7F

            if length == 126:
                raw = self._read_exact(2)
                if not raw:
                    return None
                length = struct.unpack('>H', raw)[0]
            elif length == 127:
                raw = self._read_exact(8)
                if not raw:
                    return None
                length = struct.unpack('>Q', raw)[0]

            if masked:
                mask = self._read_exact(4)
                if not mask:
                    return None
            else:
                mask = None

            payload = self._read_exact(length)
            if payload is None:
                return None

            if mask:
                payload = bytearray(payload)
                for i in range(len(payload)):
                    payload[i] ^= mask[i % 4]
                payload = bytes(payload)

            if opcode == 0x8:  # close
                self.closed = True
                return None
            if opcode == 0x9:  # ping
                self.send_pong(payload)
                return self.recv()  # recurse to get next real frame

            return (opcode, payload)
        except Exception:
            self.closed = True
            return None

    def send_pong(self, data):
        header = bytearray([0x80 | 0xA, len(data)])
        try:
            self.sock.sendall(bytes(header) + data)
        except Exception:
            self.closed = True

    def _read_exact(self, n):
        data = b''
        while len(data) < n:
            try:
                chunk = self.sock.recv(n - len(data))
                if not chunk:
                    return None
                data += chunk
            except Exception:
                return None
        return data

    def close(self):
        self.closed = True
        try:
            self.sock.close()
        except Exception:
            pass


def do_handshake(sock):
    """Perform WebSocket handshake. Returns (WebSocketConnection, path) or None."""
    try:
        data = b''
        while b'\r\n\r\n' not in data:
            chunk = sock.recv(4096)
            if not chunk:
                return None, None
            data += chunk

        headers = data.decode('utf-8', errors='replace')
        lines = headers.split('\r\n')

        # Parse request line
        parts = lines[0].split(' ')
        path = parts[1] if len(parts) > 1 else '/'

        # Find Sec-WebSocket-Key
        key = None
        for line in lines[1:]:
            if line.lower().startswith('sec-websocket-key:'):
                key = line.split(':', 1)[1].strip()
                break

        if not key:
            return None, None

        # Compute accept key
        GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
        accept = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()
        ).decode()

        response = (
            'HTTP/1.1 101 Switching Protocols\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Accept: {accept}\r\n'
            '\r\n'
        )
        sock.sendall(response.encode())

        return WebSocketConnection(sock, sock.getpeername()), path
    except Exception as e:
        log.error("Handshake failed: %s", e)
        return None, None


class Bridge:
    """Routes messages between extension and controller."""

    def __init__(self):
        self.extension = None  # WebSocketConnection
        self.controllers = []  # list of WebSocketConnection
        self.lock = threading.Lock()

    def register(self, ws, role):
        with self.lock:
            if role == 'extension':
                if self.extension:
                    log.info("Extension reconnecting, closing old")
                    self.extension.close()
                self.extension = ws
                ws.role = 'extension'
                log.info("Extension connected")
            else:
                ws.role = 'controller'
                self.controllers.append(ws)
                log.info("Controller connected (total: %d)", len(self.controllers))

    def unregister(self, ws):
        with self.lock:
            if ws.role == 'extension' and self.extension is ws:
                self.extension = None
                log.info("Extension disconnected")
            elif ws.role == 'controller':
                self.controllers = [c for c in self.controllers if c is not ws]
                log.info("Controller disconnected")

    def forward_to_extension(self, msg_str):
        """Forward command from controller to extension."""
        with self.lock:
            if self.extension and not self.extension.closed:
                self.extension.send(msg_str)
                return True
        return False

    def forward_to_controller(self, msg_str, target_id=None):
        """Forward response from extension to controller(s)."""
        with self.lock:
            for c in self.controllers:
                if not c.closed:
                    c.send(msg_str)


bridge = Bridge()


def _check_token(path):
    """Validate authentication token from query string if WS_TOKEN is set."""
    if not WS_TOKEN:
        return True  # No token configured, allow all
    try:
        parsed = urlparse(path)
        params = parse_qs(parsed.query)
        token = params.get("token", [None])[0]
        if not token:
            return False
        return hmac.compare_digest(token, WS_TOKEN)
    except Exception:
        return False


def handle_client(sock, addr):
    ws, path = do_handshake(sock)
    if not ws:
        sock.close()
        return

    # Determine role from path
    # /extension -> Chrome extension
    # /controller or anything else -> MCP controller
    if '/extension' in (path or ''):
        role = 'extension'
        # Authenticate extension connections (token required)
        if not _check_token(path or '/'):
            log.warning("Auth rejected from %s (invalid token) path=%s bridge_token_len=%d", addr, path, len(WS_TOKEN))
            ws.close()
            return
    else:
        role = 'controller'

    bridge.register(ws, role)

    try:
        while not ws.closed:
            frame = ws.recv()
            if frame is None:
                break

            opcode, payload = frame
            if opcode == 0x1:  # text
                msg_str = payload.decode('utf-8', errors='replace')

                if role == 'extension':
                    # Extension sends responses -> forward to controllers
                    bridge.forward_to_controller(msg_str)
                else:
                    # Controller sends commands -> forward to extension
                    if not bridge.forward_to_extension(msg_str):
                        # Extension not connected
                        try:
                            msg = json.loads(msg_str)
                            error_resp = json.dumps({
                                "type": "error",
                                "id": msg.get("id", 0),
                                "error": "Extension not connected"
                            })
                            ws.send(error_resp)
                        except Exception:
                            ws.send('{"type":"error","error":"Extension not connected"}')
    except Exception as e:
        log.error("Client handler error: %s", e)
    finally:
        bridge.unregister(ws)
        ws.close()


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bind_addr = os.environ.get("SCREENBOX_WS_BIND", "0.0.0.0")
    server.bind((bind_addr, PORT))
    server.listen(5)
    log.info("WS bridge listening on port %d", PORT)

    try:
        while True:
            client_sock, addr = server.accept()
            t = threading.Thread(target=handle_client, args=(client_sock, addr), daemon=True)
            t.start()
    except KeyboardInterrupt:
        log.info("Shutting down")
    finally:
        server.close()


if __name__ == '__main__':
    main()
