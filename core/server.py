"""Local HTTP server for the dummy domains.

Binds to 127.0.0.1 only, so other computers on the network cannot reach it.
Only domains that are listed AND enabled in the registry are answered.
Any other host name gets a 404 response.

HTTPS is added in a later step, once HTTP behaves correctly.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

from . import banner
from .logs import RequestLog
from .registry import Registry

EMPTY_PAGE = b"<!doctype html><html><head><meta charset='utf-8'></head><body></body></html>"


class DummyHandler(BaseHTTPRequestHandler):
    # Set by the server factory below.
    registry: Registry
    log: RequestLog

    server_version = "DummySite"

    def do_GET(self):  # noqa: N802 (name required by http.server)
        self._handle()

    def do_HEAD(self):  # noqa: N802
        self._handle(head_only=True)

    def _host(self) -> str:
        raw = self.headers.get("Host", "") or ""
        return raw.split(":")[0].strip().lower().rstrip(".")

    def _handle(self, head_only: bool = False) -> None:
        host = self._host()
        path = self.path.split("?")[0]
        entry = self.registry.get(host)

        if entry is None or not entry.enabled:
            self.log.record(host, path, "not_found")
            self._send(404, b"Not found", "text/plain; charset=utf-8", head_only)
            return

        content_path, result = self._resolve_content(entry)
        if content_path is None:
            body = EMPTY_PAGE
            content_type = "text/html; charset=utf-8"
        else:
            body = content_path.read_bytes()
            content_type = entry.content_type if entry.content_file else self._default_type(content_path)

        body = banner.inject(body, content_type)
        self.log.record(host, path, result)
        self._send(200, body, content_type, head_only)

    def _resolve_content(self, entry) -> tuple[Optional[Path], str]:
        if entry.content_file:
            path = self.registry.content_dir / entry.content_file
            if path.is_file():
                return path, "served_custom"
        default = self.registry.default_path()
        if default is not None:
            return default, "served_default"
        return None, "served_empty"

    @staticmethod
    def _default_type(path: Path) -> str:
        return "text/html; charset=utf-8"

    def _send(self, status: int, body: bytes, content_type: str, head_only: bool) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def log_message(self, format, *args):  # silence default stderr logging
        return


class DummyServer:
    def __init__(
        self,
        registry: Registry,
        log: RequestLog,
        host: str = "127.0.0.1",
        port: int = 80,
        ssl_context=None,
    ):
        handler = type("BoundHandler", (DummyHandler,), {"registry": registry, "log": log})
        self._httpd = ThreadingHTTPServer((host, port), handler)
        if ssl_context is not None:
            # Wrap the listening socket; each accepted connection gets TLS with the SNI-selected certificate.
            self._httpd.socket = ssl_context.wrap_socket(self._httpd.socket, server_side=True)
        self._thread: Optional[threading.Thread] = None

    @property
    def address(self) -> tuple[str, int]:
        return self._httpd.server_address[:2]

    def start(self) -> None:
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)
