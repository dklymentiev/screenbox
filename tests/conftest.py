"""Shared fixtures for Screenbox tests."""

import json
import os
import socket
import struct
import base64
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def free_port():
    """Get a free TCP port."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ws_handshake_request(path="/controller"):
    """Build a WebSocket upgrade request."""
    key = base64.b64encode(os.urandom(16)).decode()
    return (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: 127.0.0.1\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"\r\n"
    ).encode()


def ws_encode_frame(payload: str, masked=True) -> bytes:
    """Encode a WebSocket text frame."""
    data = payload.encode("utf-8")
    header = bytearray([0x81])  # FIN + text

    length = len(data)
    if masked:
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack(">Q", length))
        mask = os.urandom(4)
        header.extend(mask)
        masked_data = bytearray(data)
        for i in range(len(masked_data)):
            masked_data[i] ^= mask[i % 4]
        return bytes(header) + bytes(masked_data)
    else:
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(struct.pack(">H", length))
        else:
            header.append(127)
            header.extend(struct.pack(">Q", length))
        return bytes(header) + data


def ws_decode_frame(sock, timeout=5) -> str:
    """Decode a WebSocket text frame from socket."""
    sock.settimeout(timeout)
    b1 = sock.recv(1)
    b2 = sock.recv(1)
    length = b2[0] & 0x7F
    masked = b2[0] & 0x80

    if length == 126:
        length = struct.unpack(">H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", sock.recv(8))[0]

    if masked:
        mask = sock.recv(4)
    else:
        mask = None

    payload = b""
    while len(payload) < length:
        payload += sock.recv(length - len(payload))

    if mask:
        payload = bytearray(payload)
        for i in range(len(payload)):
            payload[i] ^= mask[i % 4]
        payload = bytes(payload)

    return payload.decode("utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_config_dir(tmp_path):
    """Temporary directory for Config tests."""
    return tmp_path



@pytest.fixture
def ws_bridge_port():
    """Free port for WS bridge tests."""
    return free_port()
