"""A small HTTP app for QuadDeploy demos.

Set HEALTHY=0 to make /healthz fail, which shows a rollback.
"""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = os.environ.get("APP_VERSION", "dev")
HEALTHY = os.environ.get("HEALTHY", "1") != "0"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/healthz":
            self._reply(200 if HEALTHY else 503, "ok" if HEALTHY else "unhealthy")
        elif self.path == "/":
            self._reply(200, f"QuadDeploy demo, version {VERSION}")
        else:
            self._reply(404, "not found")

    def _reply(self, status: int, text: str) -> None:
        body = f"{text}\n".encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
