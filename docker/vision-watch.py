#!/usr/bin/env python3
"""Vision Watch -- smart watching modes on top of vision-stream.py WebSocket.

Connects to a running vision-stream.py and demonstrates two watching modes:
  - stabilize: wait until screen stops changing (page load, dialog settle)
  - change: wait until something changes (notification, content update)

Usage:
    python3 vision-watch.py [host] [port] [mode] [region] [timeout]

Examples:
    python3 vision-watch.py 127.0.0.1 8766 stabilize 0,0,1920,1080 5
    python3 vision-watch.py 127.0.0.1 8766 change 230,100,750,530 10
"""

import base64
import hashlib
import json
import os
import socket
import struct
import sys
import time

RESULT_PATH = "/tmp/vision-watch-result.jpg"


# ---------------------------------------------------------------------------
# Minimal WebSocket client (from conftest.py, proven working)
# ---------------------------------------------------------------------------

class WSClient:
    """Minimal RFC 6455 WebSocket client. No dependencies."""

    def __init__(self, host, port, timeout=30):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect((host, int(port)))
        self._handshake(host, port)

    def _handshake(self, host, port):
        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            f"GET / HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        )
        self.sock.sendall(req.encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("Handshake failed: connection closed")
            resp += chunk
        if b"101" not in resp.split(b"\r\n")[0]:
            raise ConnectionError(f"Handshake rejected: {resp[:200]}")

    def send_json(self, obj):
        payload = json.dumps(obj)
        self._send_frame(payload.encode("utf-8"))

    def recv_json(self, timeout=5):
        self.sock.settimeout(timeout)
        raw = self._recv_frame()
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        return json.loads(raw)

    def _send_frame(self, data, opcode=0x1):
        header = bytearray([0x80 | opcode])
        length = len(data)
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
        masked = bytearray(data)
        for i in range(len(masked)):
            masked[i] ^= mask[i % 4]
        self.sock.sendall(bytes(header) + bytes(masked))

    def _recv_frame(self):
        b1 = self._read(1)
        if not b1:
            return None
        b2 = self._read(1)
        if not b2:
            return None
        opcode = b1[0] & 0x0F
        masked = b2[0] & 0x80
        length = b2[0] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self._read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self._read(8))[0]
        mask = self._read(4) if masked else None
        payload = self._read(length)
        if payload is None:
            return None
        if mask:
            payload = bytearray(payload)
            for i in range(len(payload)):
                payload[i] ^= mask[i % 4]
            payload = bytes(payload)
        if opcode == 0x8:
            return None
        if opcode == 0x9:  # ping
            self._send_frame(payload, opcode=0xA)  # pong
            return self._recv_frame()
        return payload.decode("utf-8", errors="replace") if opcode == 0x1 else payload

    def _read(self, n):
        data = b""
        while len(data) < n:
            chunk = self.sock.recv(n - len(data))
            if not chunk:
                return None
            data += chunk
        return data

    def close(self):
        try:
            self._send_frame(b"", opcode=0x8)  # close frame
        except Exception:
            pass
        try:
            self.sock.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Watch modes
# ---------------------------------------------------------------------------

def log(msg):
    print(f"[watch] {msg}", flush=True)


def subscribe(ws, region, fps=5, threshold=0.01):
    """Send subscribe and wait for ack."""
    ws.send_json({
        "type": "subscribe",
        "region": region,
        "fps": fps,
        "threshold": threshold,
    })
    # Consume ack/state messages
    for _ in range(5):
        msg = ws.recv_json(timeout=3)
        if msg is None:
            break
        if msg.get("type") == "ack":
            return msg
        if msg.get("type") == "state":
            return msg
    return None


def save_jpeg(image_b64, path=RESULT_PATH):
    """Save base64-encoded JPEG to file."""
    data = base64.b64decode(image_b64)
    try:
        os.remove(path)
    except OSError:
        pass
    with open(path, "wb") as f:
        f.write(data)


def request_snapshot(ws, timeout=3):
    """Request a full snapshot frame and return it."""
    ws.send_json({"type": "snapshot"})
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msg = ws.recv_json(timeout=max(0.5, deadline - time.monotonic()))
        if msg is None:
            break
        if msg.get("type") == "frame" and msg.get("mode") == "full":
            return msg
    return None


def watch_stabilize(ws, region, fps=5, timeout=5, threshold=0.01):
    """Wait until screen stops changing.

    Returns dict with stabilized, elapsed, total_frames, changed_frames.
    """
    STABLE_COUNT = 3
    consecutive_none = 0
    total_frames = 0
    changed_frames = 0
    last_image = None

    log(f"Mode: stabilize, region={region}, timeout={timeout}s")

    subscribe(ws, region, fps, threshold)
    start = time.monotonic()

    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            break

        remaining = timeout - elapsed
        msg = ws.recv_json(timeout=min(2.0, remaining + 0.5))
        if msg is None:
            continue
        if msg.get("type") != "frame":
            continue

        total_frames += 1
        seq = msg.get("seq", total_frames)
        mode = msg.get("mode", "none")

        if mode == "full":
            changed_frames += 1
            consecutive_none = 0
            last_image = msg.get("image")
            size = len(last_image) if last_image else 0
            log(f"Frame {seq}: full (I-frame), {size}B")
        elif mode == "diff":
            changed_frames += 1
            consecutive_none = 0
            blocks = msg.get("blocks_count", len(msg.get("blocks", [])))
            pct = msg.get("changed_pct", 0) * 100
            log(f"Frame {seq}: diff, {blocks} blocks, {pct:.1f}% changed")
        elif mode == "none":
            consecutive_none += 1
            log(f"Frame {seq}: none")
            if consecutive_none >= STABLE_COUNT:
                elapsed = time.monotonic() - start
                log(f"STABILIZED after {elapsed:.1f}s ({total_frames} frames, {changed_frames} changed)")
                # Get final snapshot for saving
                if last_image is None:
                    snap = request_snapshot(ws)
                    if snap:
                        last_image = snap.get("image")
                if last_image:
                    save_jpeg(last_image)
                    log(f"Last frame saved to {RESULT_PATH}")
                return {
                    "stabilized": True,
                    "elapsed": round(elapsed, 2),
                    "total_frames": total_frames,
                    "changed_frames": changed_frames,
                }

    elapsed = time.monotonic() - start
    log(f"TIMEOUT after {elapsed:.1f}s ({total_frames} frames, {changed_frames} changed)")
    # Save whatever we have
    if last_image is None:
        snap = request_snapshot(ws)
        if snap:
            last_image = snap.get("image")
    if last_image:
        save_jpeg(last_image)
        log(f"Last frame saved to {RESULT_PATH}")
    return {
        "stabilized": False,
        "elapsed": round(elapsed, 2),
        "total_frames": total_frames,
        "changed_frames": changed_frames,
    }


def watch_change(ws, region, fps=5, timeout=10, baseline_duration=0.5, threshold=0.005):
    """Wait until something changes on screen.

    Returns dict with changed, elapsed, total_frames.
    """
    total_frames = 0
    last_image = None

    log(f"Mode: change, region={region}, timeout={timeout}s")

    subscribe(ws, region, fps, threshold)
    start = time.monotonic()

    # -- Baseline phase: capture initial state --
    baseline_frames = 0
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= baseline_duration:
            break
        if elapsed >= timeout:
            break

        remaining = min(baseline_duration, timeout) - elapsed
        msg = ws.recv_json(timeout=min(2.0, remaining + 0.5))
        if msg is None:
            continue
        if msg.get("type") != "frame":
            continue

        total_frames += 1
        baseline_frames += 1
        if msg.get("mode") == "full" and msg.get("image"):
            last_image = msg.get("image")

    bl_elapsed = time.monotonic() - start
    log(f"Baseline: {baseline_frames} frames ({bl_elapsed:.1f}s), screen captured")
    log("Waiting for change...")

    # -- Watch phase: detect any change --
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            break

        remaining = timeout - elapsed
        msg = ws.recv_json(timeout=min(2.0, remaining + 0.5))
        if msg is None:
            continue
        if msg.get("type") != "frame":
            continue

        total_frames += 1
        seq = msg.get("seq", total_frames)
        mode = msg.get("mode", "none")

        if mode == "none":
            log(f"Frame {seq}: none")
        elif mode in ("diff", "full"):
            if mode == "diff":
                blocks = msg.get("blocks_count", len(msg.get("blocks", [])))
                pct = msg.get("changed_pct", 0) * 100
                log(f"Frame {seq}: diff, {blocks} blocks, {pct:.1f}% changed")
            else:
                size = len(msg.get("image", ""))
                log(f"Frame {seq}: full (I-frame), {size}B")
                if msg.get("image"):
                    last_image = msg["image"]

            elapsed = time.monotonic() - start
            log(f"CHANGE DETECTED after {elapsed:.1f}s ({total_frames} frames)")
            # Get snapshot to save
            if last_image is None or mode == "diff":
                snap = request_snapshot(ws)
                if snap and snap.get("image"):
                    last_image = snap["image"]
            if last_image:
                save_jpeg(last_image)
                log(f"Last frame saved to {RESULT_PATH}")
            return {
                "changed": True,
                "elapsed": round(elapsed, 2),
                "total_frames": total_frames,
            }

    elapsed = time.monotonic() - start
    log(f"TIMEOUT after {elapsed:.1f}s ({total_frames} frames, no change)")
    if last_image:
        save_jpeg(last_image)
        log(f"Last frame saved to {RESULT_PATH}")
    return {
        "changed": False,
        "elapsed": round(elapsed, 2),
        "total_frames": total_frames,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_region(s):
    """Parse 'x1,y1,x2,y2' into [x1, y1, x2, y2]."""
    parts = [int(x.strip()) for x in s.split(",")]
    if len(parts) != 4:
        raise ValueError(f"Region must be x1,y1,x2,y2 -- got {s}")
    return parts


def main():
    host = sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"
    port = int(sys.argv[2]) if len(sys.argv) > 2 else 8766
    mode = sys.argv[3] if len(sys.argv) > 3 else "stabilize"
    region_str = sys.argv[4] if len(sys.argv) > 4 else "0,0,1920,1080"
    timeout = float(sys.argv[5]) if len(sys.argv) > 5 else 5.0

    region = parse_region(region_str)

    log(f"Connecting to ws://{host}:{port} ...")
    ws = WSClient(host, port)
    log("Connected")

    try:
        if mode == "stabilize":
            result = watch_stabilize(ws, region, fps=5, timeout=timeout)
        elif mode == "change":
            result = watch_change(ws, region, fps=5, timeout=timeout)
        else:
            log(f"Unknown mode: {mode}. Use 'stabilize' or 'change'.")
            sys.exit(1)

        # Machine-readable result on last line
        print(json.dumps(result))
    except KeyboardInterrupt:
        log("Interrupted")
    except Exception as e:
        log(f"Error: {e}")
        sys.exit(1)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
