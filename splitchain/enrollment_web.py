"""Isolated web intake and loopback-only operator approval for testnet wallets."""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import html
import json
import os
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar
from urllib.parse import parse_qs

from .distributor import atomic_json, private_directory, read_json
from .enrollment import EnrollmentError, activate, approve, request_from_file
from .model import GenesisConfig

MAX_BODY = 2048
MAX_PENDING = 200


class EnrollmentStore:
    def __init__(self, queue: Path, registry: Path, genesis: GenesisConfig):
        private_directory(queue)
        self.queue, self.registry, self.genesis = queue, registry, genesis
        self.lock = threading.RLock()

    def submit(self, body: bytes) -> dict:
        try:
            document = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise EnrollmentError("invalid JSON request") from exc
        if not isinstance(document, dict):
            raise EnrollmentError("invalid request")
        # Validate the same structure as the operator's private-file CLI.
        if set(document) != {"schema", "network_id", "account", "credential"} or (
            document["schema"] != "splitchain-wallet-enrollment/v1"
            or document["network_id"] != self.genesis.network_id
        ):
            raise EnrollmentError("invalid testnet request")
        actor, credential = document["account"], document["credential"]
        if (not isinstance(actor, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", actor)
                or actor.startswith("testnet_") or actor in self.genesis.allocations
                or not isinstance(credential, str) or not re.fullmatch(r"[0-9a-f]{64}", credential)):
            raise EnrollmentError("invalid wallet ID or credential")
        with self.lock:
            target = self.queue / f"{actor}.json"
            if target.exists():
                saved = read_json(target)
                if not hmac.compare_digest(saved["credential"], credential):
                    raise EnrollmentError("wallet ID already requested")
                return {"account": actor, "receipt": saved["receipt"], "status": saved["status"]}
            if len(list(self.queue.glob("*.json"))) >= MAX_PENDING:
                raise EnrollmentError("enrollment queue is full")
            receipt = secrets.token_hex(24)
            atomic_json(target, {**document, "receipt": receipt, "status": "pending"})
            return {"account": actor, "receipt": receipt, "status": "pending"}

    def check(self, body: bytes) -> dict:
        try:
            query = json.loads(body)
            actor, receipt = query["account"], query["receipt"]
            if not isinstance(actor, str) or not isinstance(receipt, str) or not actor.isascii():
                raise ValueError()
            if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", actor):
                raise ValueError()
            saved = read_json(self.queue / f"{actor}.json")
            if not hmac.compare_digest(saved["receipt"], receipt):
                raise ValueError()
            return {"account": actor, "status": saved["status"]}
        except (OSError, ValueError, TypeError, KeyError):
            raise EnrollmentError("request not found") from None

    def approve(self, actor: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", actor):
            raise EnrollmentError("invalid account")
        with self.lock:
            target = self.queue / f"{actor}.json"
            saved = read_json(target)
            if saved["status"] == "active":
                return "active"
            # The CLI validator expects exactly the original four fields.
            request_path = self.queue / f"{actor}.wallet.json"
            atomic_json(request_path, {key: saved[key] for key in (
                "schema", "network_id", "account", "credential")})
            try:
                request_from_file(request_path, self.genesis)
                approve(request_path, self.registry, self.genesis)
            finally:
                request_path.unlink(missing_ok=True)
            if saved["status"] != "approved":
                saved["status"] = "approved"
                atomic_json(target, saved)
            try:
                asyncio.run(activate(actor, self.registry, self.genesis))
            except EnrollmentError:
                # Keep approved for a later retry; no second credential is generated.
                return "approved"
            saved["status"] = "active"
            atomic_json(target, saved)
            return "active"

    def pending(self) -> list[dict]:
        with self.lock:
            return [{"account": p.stem, "status": read_json(p)["status"]}
                    for p in sorted(self.queue.glob("*.json"))]


class PublicHandler(BaseHTTPRequestHandler):
    store: EnrollmentStore
    limiter: ClassVar[dict[str, tuple[float, int]]] = {}
    limiter_lock = threading.Lock()

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def do_POST(self) -> None:
        import time
        if self.path not in ("/request", "/check") or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self.reply(404, {"error": "unknown endpoint"})
            return
        address = self.headers.get("X-Client-IP", "unknown")[:64]
        with self.limiter_lock:
            started, count = self.limiter.get(address, (time.monotonic(), 0))
            if time.monotonic() - started >= 60:
                started, count = time.monotonic(), 0
            self.limiter[address] = (started, count + 1)
            if len(self.limiter) > 2048:
                self.limiter.clear()
        if count >= 12:
            self.reply(429, {"error": "rate limit exceeded"})
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length <= MAX_BODY:
                raise EnrollmentError("request is too large")
            body = self.rfile.read(length)
            result = self.store.submit(body) if self.path == "/request" else self.store.check(body)
            self.reply(200, result)
        except (EnrollmentError, OSError, ValueError, KeyError) as exc:
            self.reply(400, {"error": str(exc)})

    def reply(self, code: int, result: dict) -> None:
        payload = json.dumps(result).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class AdminHandler(BaseHTTPRequestHandler):
    store: EnrollmentStore
    password: str

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(5)

    def authenticated(self) -> bool:
        try:
            kind, token = self.headers.get("Authorization", "").split(" ", 1)
            credentials = base64.b64decode(token, validate=True).decode()
            user, supplied = credentials.split(":", 1)
            return kind == "Basic" and user == "operator" and hmac.compare_digest(supplied, self.password)
        except (ValueError, UnicodeDecodeError, binascii.Error):
            return False

    def require_auth(self) -> bool:
        if self.authenticated():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="SplitChain operator"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def do_GET(self) -> None:
        if not self.require_auth():
            return
        if self.path != "/":
            self.send_error(404)
            return
        rows = "".join(
            f'<li>{html.escape(entry["account"])} — {entry["status"]} '
            f'<form method="post" action="/approve" style="display:inline">'
            f'<input type="hidden" name="account" value="{html.escape(entry["account"])}">'
            f'<button>Approve / retry</button></form></li>'
            for entry in self.store.pending() if entry["status"] != "active"
        )
        page = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                '<title>SplitChain enrollment</title><h1>Pending wallet requests</h1>'
                '<p>Credentials are never displayed. Review IDs before approving.</p><ul>'
                + (rows or '<li>No pending requests</li>') + '</ul>')
        data = page.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; form-action 'self'; style-src 'unsafe-inline'")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self) -> None:
        if not self.require_auth():
            return
        host = self.headers.get("Host", "")
        allowed_hosts = {f"127.0.0.1:{self.server.server_port}",
                         f"localhost:{self.server.server_port}"}
        if (self.path != "/approve" or host not in allowed_hosts
                or self.headers.get("Origin") != f"http://{host}"):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if not 0 < length < 128:
                raise ValueError()
            actor = parse_qs(self.rfile.read(length).decode())["account"][0]
            status = self.store.approve(actor)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("X-Enrollment-Status", status)
            self.send_header("Content-Length", "0")
            self.end_headers()
        except (OSError, ValueError, KeyError, EnrollmentError):
            self.send_error(400, "approval failed; inspect operator logs and retry")


def main() -> None:
    password = os.environ["TESTNET_ENROLLMENT_PASSWORD"]
    if len(password) < 32:
        raise ValueError("operator dashboard password must contain at least 32 characters")
    registry = Path(os.environ.get("TESTNET_AUTH_FILE", "/operator/auth/accounts.json"))
    queue = Path(os.environ.get("TESTNET_ENROLLMENT_QUEUE", "/operator/enrollment-queue"))
    genesis = GenesisConfig.from_dict(read_json(Path("/etc/splitchain/testnet-genesis.json")))
    store = EnrollmentStore(queue, registry, genesis)
    PublicHandler.store = AdminHandler.store = store
    AdminHandler.password = password
    public = ThreadingHTTPServer(("0.0.0.0", 8090), PublicHandler)
    admin = ThreadingHTTPServer(("0.0.0.0", 8091), AdminHandler)
    threading.Thread(target=admin.serve_forever, daemon=True).start()
    public.serve_forever()


if __name__ == "__main__":
    main()
