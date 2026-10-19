"""Trusted malware-scanner endpoint for zhanggui (Plan A: ClamAV + HTTP shim).

Implements exactly the contract expected by ``app.ingestion.HTTPMalwareScanner``:

    POST /scan   raw body (Content-Type: application/octet-stream)
                 -> 200 {"status":"clean"}       clamd attested the stream clean
                 -> 200 {"status":"infected"}    clamd reported a match (blocked)
                 -> 503 {"status":"unavailable"} clamd unreachable (retryable)

    GET /health  -> 200 when clamd answers PING/PONG, else 503

Bytes are streamed to clamd over the INSTREAM protocol and never written to
disk. Standard library only: no third-party dependencies, minimal attack surface.
"""
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CLAMD_HOST = os.environ.get("CLAMD_HOST", "127.0.0.1")
CLAMD_PORT = int(os.environ.get("CLAMD_PORT", "3310"))
PORT = int(os.environ.get("PORT", "8080"))
MAX_BODY = int(os.environ.get("MAX_BODY_BYTES", str(64 * 1024 * 1024)))
CLAMD_TIMEOUT = float(os.environ.get("CLAMD_TIMEOUT", "60"))
CHUNK = 8192


def _connect():
    return socket.create_connection((CLAMD_HOST, CLAMD_PORT), timeout=CLAMD_TIMEOUT)


def clamd_ping() -> bool:
    with _connect() as sock:
        sock.sendall(b"zPING\0")
        sock.settimeout(CLAMD_TIMEOUT)
        return b"PONG" in sock.recv(4096)


def clamd_scan(data: bytes) -> str:
    with _connect() as sock:
        sock.sendall(b"zINSTREAM\0")
        for offset in range(0, len(data), CHUNK):
            part = data[offset:offset + CHUNK]
            sock.sendall(len(part).to_bytes(4, "big") + part)
        sock.sendall((0).to_bytes(4, "big"))
        sock.settimeout(CLAMD_TIMEOUT)
        response = b""
        while b"\n" not in response and b"\0" not in response and len(response) < 4096:
            part = sock.recv(4096 - len(response))
            if not part:
                break
            response += part
    return response.decode("utf-8", errors="ignore").replace("\0", "").strip()


def verdict(data: bytes) -> tuple[int, dict]:
    try:
        line = clamd_scan(data)
    except OSError:
        return 503, {"status": "unavailable"}
    if "FOUND" in line:
        return 200, {"status": "infected"}
    if "OK" in line:
        return 200, {"status": "clean"}
    return 503, {"status": "unavailable"}


class Handler(BaseHTTPRequestHandler):
    server_version = "zhanggui-scanner/1.0"
    protocol_version = "HTTP/1.0"

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/") in ("/health", "/healthz"):
            try:
                ready = clamd_ping()
            except OSError:
                ready = False
            if ready:
                return self._send(200, {"status": "ok"})
            return self._send(503, {"status": "unavailable"})
        return self._send(404, {"status": "not_found"})

    def do_POST(self):
        if self.path.rstrip("/") != "/scan":
            return self._send(404, {"status": "not_found"})
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            length = 0
        if length <= 0:
            return self._send(400, {"status": "empty"})
        if length > MAX_BODY:
            return self._send(413, {"status": "too_large"})
        data = self.rfile.read(length)
        code, body = verdict(data)
        self._send(code, body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
