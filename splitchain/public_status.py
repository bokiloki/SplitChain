"""Read-only HTTP status surface for a closed, valueless testnet pilot."""

from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from .bootstrap import manifest
from .model import GenesisConfig, ProtocolError

BACKENDS = (
    "ws://primary:8765", "ws://secondary:8765", "ws://tertiary:8765",
)
ENROLLMENT_BACKEND = "http://enrollment:8090"
ROUTES = {"/status": "status", "/leadership": "cluster.leadership"}
EXPLORER_PAGES = {"/explore/genesis", "/explore/status", "/explore/leadership", "/explore/bootstrap", "/explore/nodes"}
_nodes_limits: dict[str, tuple[float, int]] = {}
_nodes_limits_lock = threading.Lock()
_NODES_WINDOW = 60.0
_NODES_LIMIT = 30


def allow_nodes_request(client: str, now: float | None = None) -> bool:
    """Bound expensive three-backend probes per source address."""
    current = time.monotonic() if now is None else now
    with _nodes_limits_lock:
        started, count = _nodes_limits.get(client, (current, 0))
        if current - started >= _NODES_WINDOW:
            started, count = current, 0
        if count >= _NODES_LIMIT:
            _nodes_limits[client] = (started, count)
            return False
        _nodes_limits[client] = (started, count + 1)
        if len(_nodes_limits) > 2048:
            _nodes_limits.clear()
        return True


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


def fetch_node_status(backend: str) -> dict:
    """Probe one configured node, returning a public summary of its ledger head."""

    name = backend.removeprefix("ws://").split(":")[0]
    try:
        with connect(backend, open_timeout=1.5, close_timeout=1, max_size=64 * 1024) as socket:
            socket.send(json.dumps({"id": "node-status", "method": "status", "params": {}}))
            response = json.loads(socket.recv(timeout=1.5))
            result = response["result"]
            head = result["canonical_head"]
            if not isinstance(head["height"], int) or not isinstance(head["digest"], str):
                raise TypeError("invalid ledger head")
            return {"name": name, "state": "online", "height": head["height"],
                    "digest": head["digest"], "round": result["round"]}
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, WebSocketException):
        return {"name": name, "state": "unavailable", "height": None,
                "digest": None, "round": None}


def fetch_nodes() -> dict:
    """Probe each configured node independently without exposing internal addresses."""

    with ThreadPoolExecutor(max_workers=len(BACKENDS)) as pool:
        nodes = list(pool.map(fetch_node_status, BACKENDS))
    online = sum(node["state"] == "online" for node in nodes)
    heads = {(node["height"], node["digest"]) for node in nodes if node["state"] == "online"}
    return {"nodes": nodes, "online": online, "total": len(nodes),
            "heads_agree": online == len(nodes) and len(heads) == 1}


class Handler(BaseHTTPRequestHandler):
    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def do_GET(self) -> None:
        if self.path == "/downloads":
            try:
                base = os.environ.get("TESTNET_BOOTSTRAP_URL", "https://bokiloki.ddns.net/splitchain/")
                body = (Path(__file__).parent / "web/downloads.html").read_bytes().replace(
                    b"__SPLITCHAIN_BASE__", base.encode()
                )
            except OSError:
                self._send(503, {"error": "testnet downloads unavailable"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/wallet.js":
            try:
                base = os.environ.get("TESTNET_BOOTSTRAP_URL", "https://bokiloki.ddns.net/splitchain/")
                body = (Path(__file__).parent / "web/wallet.js").read_bytes().replace(
                    b"__SPLITCHAIN_BASE__", base.encode()
                )
            except OSError:
                self._send(503, {"error": "testnet wallet unavailable"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/create-wallet":
            body = (Path(__file__).parent / "web/create-wallet.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; form-action 'none'")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path in EXPLORER_PAGES:
            try:
                base = os.environ.get("TESTNET_BOOTSTRAP_URL", "https://bokiloki.ddns.net/splitchain/")
                genesis = GenesisConfig.from_dict(json.loads(Path(os.environ.get(
                    "TESTNET_GENESIS_FILE", "/etc/splitchain/testnet-genesis.json"
                )).read_text(encoding="utf-8")))
                manifest(base, genesis)
                body = (Path(__file__).parent / "web/explorer.html").read_bytes().replace(
                    b"__SPLITCHAIN_BASE__", base.encode()
                )
            except (OSError, ValueError, ProtocolError):
                self._send(503, {"error": "testnet explorer unavailable"})
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
        if self.path == "/":
            try:
                body = (Path(__file__).parent / "web/testnet.html").read_bytes()
                base = os.environ.get("TESTNET_BOOTSTRAP_URL", "https://bokiloki.ddns.net/splitchain/")
                genesis = GenesisConfig.from_dict(json.loads(Path(os.environ.get("TESTNET_GENESIS_FILE", "/etc/splitchain/testnet-genesis.json")).read_text(encoding="utf-8")))
                rpc = manifest(base, genesis)["rpc_url"]
                body = body.replace(b"__SPLITCHAIN_BASE__", base.encode()).replace(b"__SPLITCHAIN_RPC__", rpc.encode())
            except (OSError, ValueError, ProtocolError):
                self._send(503, {"error": "testnet page unavailable"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; connect-src 'self' wss://bokiloki.ddns.net; style-src 'self' 'unsafe-inline'; object-src 'none'; base-uri 'none'; form-action 'none'")
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
                    "TESTNET_BOOTSTRAP_URL", "https://bokiloki.ddns.net/splitchain/"
                ), genesis) if self.path.endswith("splitchain-testnet.json") else genesis.public())
            except (OSError, ValueError, ProtocolError):
                self._send(503, {"error": "genesis unavailable"})
                return
            self._send(200, result)
            return
        if self.path == "/nodes":
            client = self.client_address[0] if self.client_address else "unknown"
            if not allow_nodes_request(client):
                self._send(429, {"error": "node status rate limit exceeded"})
                return
            self._send(200, fetch_nodes())
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
        if self.path in ("/enroll", "/enroll/check"):
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 < length <= 2048:
                    self._send(413, {"error": "invalid request length"})
                    return
                route = "/request" if self.path == "/enroll" else "/check"
                request = Request(ENROLLMENT_BACKEND + route, data=self.rfile.read(length),
                                  headers={"Content-Type": "application/json",
                                           "X-Client-IP": self.headers.get("X-Real-IP", self.client_address[0])},
                                  method="POST")
                with urlopen(request, timeout=4) as response:
                    result = json.load(response)
                self._send(200, result)
            except HTTPError as exc:
                self._send(exc.code, {"error": "enrollment request rejected"})
            except (URLError, OSError, ValueError, TimeoutError):
                self._send(503, {"error": "enrollment service unavailable"})
            return
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
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
