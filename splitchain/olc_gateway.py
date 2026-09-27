"""Small authenticated OLC pilot gateway; no arbitrary job submission."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PAYLOAD = b"OLC Worker01 test job\n"
EXPECTED = hashlib.sha256(PAYLOAD).hexdigest()
NODE_ID = "olc-worker-001"
LEASE_SECONDS = 120
ONLINE_SECONDS = 45


class Store:
    def __init__(self, path: str | Path, credentials: dict):
        self.path = Path(path)
        workers = credentials.get("workers", {})
        operator = credentials.get("operator", "")
        if (not isinstance(workers, dict) or NODE_ID not in workers
                or any(not isinstance(value, str) or len(value) < 32
                       for value in (*workers.values(), operator))
                or len({*workers.values(), operator}) != len(workers) + 1):
            raise ValueError("gateway credentials must contain distinct 32+ character tokens")
        self.credentials = credentials
        self.lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {"workers": {}, "jobs": {}}
            self._save()

    def _save(self):
        temp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as out:
            json.dump(self.data, out, separators=(",", ":"))
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, self.path)

    def authorized(self, token: str, role: str):
        reference = (self.credentials["operator"] if role == "operator" else
                     self.credentials["workers"].get(role, ""))
        return bool(reference) and hmac.compare_digest(token, reference)

    def heartbeat(self, node_id: str, capabilities: dict):
        if not isinstance(capabilities, dict) or set(capabilities) != {"cpu_threads", "memory_mb"}:
            raise ValueError("invalid capabilities")
        cpu, memory = capabilities["cpu_threads"], capabilities["memory_mb"]
        if type(cpu) is not int or not 1 <= cpu <= 256 or type(memory) is not int or not 128 <= memory <= 2_000_000:
            raise ValueError("invalid resource inventory")
        with self.lock:
            self.data["workers"][node_id] = {"last_seen": time.time(), "capabilities": capabilities}
            self._save()
        return {"accepted": True, "node_id": node_id}

    def public_workers(self):
        with self.lock:
            now = time.time()
            return {"workers": [{"node_id": name,
                    "state": "online" if now - value["last_seen"] < ONLINE_SECONDS else "offline",
                    "capabilities": value["capabilities"]}
                    for name, value in sorted(self.data["workers"].items())]}

    def enqueue(self):
        with self.lock:
            job_id = secrets.token_hex(16)
            self.data["jobs"][job_id] = {"node_id": NODE_ID, "state": "queued", "created": time.time()}
            self._save()
            return {"job_id": job_id, "state": "queued"}

    def lease(self, node_id: str):
        with self.lock:
            now = time.time()
            if any(job["node_id"] == node_id and job["state"] == "leased"
                   and job["lease_until"] >= now for job in self.data["jobs"].values()):
                return {"job": None}
            for job_id, job in self.data["jobs"].items():
                if job["node_id"] == node_id and (job["state"] == "queued" or
                        job["state"] == "leased" and job["lease_until"] < now):
                    job.update(state="leased", lease_until=now + LEASE_SECONDS)
                    self._save()
                    return {"job_id": job_id, "kind": "sha256-fixed-v1"}
            return {"job": None}

    def result(self, node_id: str, job_id: str, digest: str):
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("invalid digest")
        with self.lock:
            job = self.data["jobs"].get(job_id)
            if not job or job["node_id"] != node_id or job["state"] not in {"leased", "verified", "rejected"}:
                raise ValueError("unknown or unleased job")
            if job["state"] == "leased":
                if time.time() > job["lease_until"]:
                    raise ValueError("job lease expired")
                job.update(state="verified" if hmac.compare_digest(digest, EXPECTED) else "rejected",
                           result_digest=digest, completed=time.time())
                self._save()
            elif job["result_digest"] != digest:
                raise ValueError("conflicting result")
            return {"job_id": job_id, "state": job["state"], "verification": "coordinator-sha256"}

    def operator_jobs(self):
        with self.lock:
            return {"jobs": [{"job_id": key, **value} for key, value in self.data["jobs"].items()]}


class Handler(BaseHTTPRequestHandler):
    store: Store

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def _send(self, status: int, value: dict):
        body = json.dumps(value, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorize(self, role):
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or not self.store.authorized(header[7:], role):
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _body(self):
        size = int(self.headers.get("Content-Length", "-1"))
        if not 0 <= size <= 2048:
            raise ValueError("invalid body length")
        value = json.loads(self.rfile.read(size))
        if not isinstance(value, dict):
            raise TypeError("body must be an object")
        return value

    def do_GET(self):
        if self.path == "/workers":
            return self._send(200, self.store.public_workers())
        if self.path == f"/worker/{NODE_ID}/job":
            if self._authorize(NODE_ID):
                self._send(200, self.store.lease(NODE_ID))
            return
        if self.path == "/operator/jobs":
            if self._authorize("operator"):
                self._send(200, self.store.operator_jobs())
            return
        self._send(404, {"error": "unknown route"})

    def do_POST(self):
        role = "operator" if self.path == "/operator/job" else NODE_ID
        if self.path not in {"/operator/job", f"/worker/{NODE_ID}/heartbeat", f"/worker/{NODE_ID}/result"}:
            return self._send(404, {"error": "unknown route"})
        if not self._authorize(role):
            return
        try:
            body = self._body()
            if self.path == "/operator/job":
                if body != {"kind": "sha256-fixed-v1", "node_id": NODE_ID}:
                    raise ValueError("only the fixed test workload is supported")
                result = self.store.enqueue()
            elif self.path.endswith("/heartbeat"):
                result = self.store.heartbeat(NODE_ID, body.get("capabilities"))
            else:
                result = self.store.result(NODE_ID, body.get("job_id"), body.get("digest"))
        except (ValueError, TypeError):
            return self._send(400, {"error": "invalid request"})
        self._send(200, result)


def main():
    credentials = json.loads(Path(os.environ["OLC_CREDENTIALS_FILE"]).read_text())
    Handler.store = Store(os.environ.get("OLC_STATE_FILE", "/var/lib/olc/state.json"), credentials)
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("OLC_GATEWAY_PORT", "8092"))), Handler).serve_forever()


if __name__ == "__main__":
    main()
