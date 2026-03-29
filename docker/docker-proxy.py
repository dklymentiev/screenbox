#!/usr/bin/env python3
"""Minimal Docker API proxy with endpoint whitelist.

Replaces tecnativa/docker-socket-proxy (socat-based, drops binary stdout).
Forwards allowed Docker API requests from TCP to Unix socket.
Properly streams exec output without data loss.

Usage:
    python3 docker-proxy.py [--port 2375] [--socket /var/run/docker.sock]
"""

import http.server
import http.client
import socket
import os
import re
import sys
import threading

LISTEN_PORT = int(os.environ.get("PROXY_PORT", "2375"))
DOCKER_SOCKET = os.environ.get("DOCKER_SOCKET", "/var/run/docker.sock")

# Allowed endpoints (method, path regex)
ALLOWED = [
    ("GET",    r"/v[\d.]+/containers/json"),
    ("GET",    r"/v[\d.]+/containers/[^/]+/json"),
    ("POST",   r"/v[\d.]+/containers/[^/]+/exec"),
    ("POST",   r"/v[\d.]+/exec/[^/]+/start"),
    ("POST",   r"/v[\d.]+/exec/[^/]+/resize"),
    ("GET",    r"/v[\d.]+/exec/[^/]+/json"),
    ("GET",    r"/v[\d.]+/images/json"),
    ("GET",    r"/v[\d.]+/images/[^/]+/json"),
    ("GET",    r"/v[\d.]+/networks$"),
    ("GET",    r"/v[\d.]+/networks/[^/]+$"),
    ("POST",   r"/v[\d.]+/networks/[^/]+/connect"),
    ("POST",   r"/v[\d.]+/networks/[^/]+/disconnect"),
    ("POST",   r"/v[\d.]+/containers/create"),
    ("POST",   r"/v[\d.]+/containers/[^/]+/start"),
    ("POST",   r"/v[\d.]+/containers/[^/]+/stop"),
    ("POST",   r"/v[\d.]+/containers/[^/]+/kill"),
    ("DELETE",  r"/v[\d.]+/containers/[^/]+"),
    ("POST",   r"/v[\d.]+/containers/[^/]+/wait"),
    ("GET",    r"/v[\d.]+/info"),
    ("GET",    r"/v[\d.]+/version"),
    ("GET",    r"/_ping"),
    ("HEAD",   r"/_ping"),
    ("GET",    r"/info"),
    ("GET",    r"/version"),
    # Archive (docker cp)
    ("PUT",    r"/v[\d.]+/containers/[^/]+/archive"),
    ("GET",    r"/v[\d.]+/containers/[^/]+/archive"),
    # Container stats (for dashboard cpu/mem)
    ("GET",    r"/v[\d.]+/containers/[^/]+/stats"),
    # Volumes (for named volumes)
    ("GET",    r"/v[\d.]+/volumes$"),
    ("GET",    r"/v[\d.]+/volumes/[^/]+$"),
    ("POST",   r"/v[\d.]+/volumes/create"),
]

COMPILED = [(m, re.compile(f"^{p}(\\?.*)?$")) for m, p in ALLOWED]


def is_allowed(method: str, path: str) -> bool:
    for m, pattern in COMPILED:
        if method == m and pattern.match(path):
            return True
    return False


class UnixHTTPConnection(http.client.HTTPConnection):
    """HTTP connection over Unix socket."""

    def __init__(self, socket_path):
        super().__init__("localhost")
        self.socket_path = socket_path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(self.socket_path)


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):
        pass  # Quiet

    def do_request(self):
        if not is_allowed(self.command, self.path):
            self.send_error(403, f"Blocked: {self.command} {self.path}")
            return

        # Read request body
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else None

        # Forward to Docker
        conn = UnixHTTPConnection(DOCKER_SOCKET)
        try:
            headers = {k: v for k, v in self.headers.items()
                       if k.lower() not in ("host", "transfer-encoding")}
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()

            # For exec/start: long-running raw stream (multiplexed stdout)
            is_exec_start = "/exec/" in self.path and "/start" in self.path
            if is_exec_start:
                # Forward response headers
                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    self.send_header(key, val)
                self.end_headers()
                # Raw stream relay with long timeout (exec can run for minutes)
                docker_sock = resp.fp.raw
                if hasattr(docker_sock, '_sock'):
                    docker_sock = docker_sock._sock
                docker_sock.setblocking(False)
                import select as _select
                while True:
                    readable, _, _ = _select.select([docker_sock], [], [], 30)
                    if not readable:
                        break
                    try:
                        chunk = docker_sock.recv(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                    except (BlockingIOError, OSError):
                        break
            else:
                # Standard HTTP: read full body, send with Content-Length
                body = resp.read()
                self.send_response(resp.status)
                for key, val in resp.getheaders():
                    lk = key.lower()
                    if lk in ("transfer-encoding", "connection", "content-length"):
                        continue
                    self.send_header(key, val)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)
                    self.wfile.flush()
        finally:
            conn.close()

    do_GET = do_request
    do_POST = do_request
    do_PUT = do_request
    do_DELETE = do_request
    do_HEAD = do_request


class ThreadedHTTPServer(http.server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def process_request(self, request, client_address):
        t = threading.Thread(target=self.process_request_thread,
                             args=(request, client_address))
        t.daemon = True
        t.start()

    def process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)


def main():
    port = LISTEN_PORT
    if "--port" in sys.argv:
        port = int(sys.argv[sys.argv.index("--port") + 1])
    if "--socket" in sys.argv:
        global DOCKER_SOCKET
        DOCKER_SOCKET = sys.argv[sys.argv.index("--socket") + 1]

    print(f"[docker-proxy] Listening on 0.0.0.0:{port}")
    print(f"[docker-proxy] Forwarding to {DOCKER_SOCKET}")
    print(f"[docker-proxy] Allowed endpoints: {len(COMPILED)}")

    server = ThreadedHTTPServer(("0.0.0.0", port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[docker-proxy] Shutting down")


if __name__ == "__main__":
    main()
