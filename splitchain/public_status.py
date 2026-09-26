"""Read-only HTTP status surface for a closed, valueless testnet pilot."""

from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .bootstrap import manifest
from .model import GenesisConfig, ProtocolError

BACKENDS = (
    "ws://primary:8765", "ws://secondary:8765", "ws://tertiary:8765",
)
ROUTES = {"/status": "status", "/leadership": "cluster.leadership"}


def fetch_read_only(method: str) -> dict:
    """Try the three internal nodes without accepting a user-supplied RPC method."""

    if method not in ROUTES.values():
        raise ValueError("unsupported status method")
    for backend in BACKENDS:
        try:
            with connect(backend, open_timeout=2, close_timeout=2, max_size=64 * 1024) as socket:
                socket.send(json.dumps({"id": "public-status", "method": method, "params": {}}))
                response = json.loads(socket.recv(timeout=2))
                if "result" in response:
                    return {"source": backend.removeprefix("ws://").split(":")[0],
                            "result": response["result"]}
        except (OSError, TimeoutError, ValueError, WebSocketException):
            continue
    raise ConnectionError("no node returned a valid status")


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self) -> None:
        if self.path == "/":
            try:
                body = (Path(__file__).parent / "web/testnet.html").read_bytes()
            except OSError:
                self._send(503, {"error": "testnet page unavailable"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in {"/.well-known/splitchain-testnet.json", "/genesis.json"}:
            try:
                genesis = GenesisConfig.from_dict(json.loads(Path(
                    os.environ.get("TESTNET_GENESIS_FILE", "/etc/splitchain/testnet-genesis.json")
                ).read_text(encoding="utf-8")))
                result = (manifest(os.environ.get(
                    "TESTNET_BOOTSTRAP_URL", "https://bokiloki.ddns.net/"
                ), genesis) if self.path.endswith("splitchain-testnet.json") else genesis.public())
            except (OSError, ValueError, ProtocolError):
                self._send(503, {"error": "genesis unavailable"})
                return
            self._send(200, result)
            return
        if self.path != urlsplit(self.path).path or self.path not in ROUTES:
            self._send(404, {"error": "unknown read-only endpoint"})
            return
        try:
            result = fetch_read_only(ROUTES[self.path])
        except ConnectionError:
            self._send(503, {"error": "testnet status unavailable"})
            return
        self._send(200, result)

    def do_POST(self) -> None:
        self._send(405, {"error": "transaction RPC is unavailable on the public endpoint"})

    def _send(self, status: int, value: dict) -> None:
        body = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    port = int(os.environ.get("STATUS_PORT", "8080"))
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
