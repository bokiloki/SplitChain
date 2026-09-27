"""Outbound-only pilot worker for a single allowlisted SHA-256 job."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .bootstrap import join
from .olc_gateway import NODE_ID


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("worker endpoint redirected")


def request(base: str, path: str, token: str, data: dict | None = None) -> dict:
    url = urljoin(base, path)
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Authorization": "Bearer " + token}
    if body is not None:
        headers["Content-Type"] = "application/json"
    with build_opener(NoRedirect).open(
            Request(url, body, headers=headers, method="POST" if body is not None else "GET"),
            timeout=10) as response:
        payload = response.read(4097)
        if len(payload) > 4096:
            raise ValueError("worker response too large")
        result = json.loads(payload)
        if not isinstance(result, dict):
            raise TypeError("invalid worker response")
        return result


def sandbox_job() -> str:
    info = subprocess.run(["podman", "info", "--format", "{{.Host.Security.Rootless}}"],
                          text=True, capture_output=True, timeout=15, check=True)
    if info.stdout.strip() != "true":
        raise RuntimeError("rootless Podman required")
    command = ["podman", "run", "--rm", "--pull=never", "--network", "none", "--read-only",
               "--cap-drop", "all", "--security-opt", "no-new-privileges", "--pids-limit", "32",
               "--cpus", "1", "--memory", "256m", "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
               "docker.io/library/debian:13", "sh", "-c",
               'printf "OLC Worker01 test job\\n" | sha256sum']
    completed = subprocess.run(command, text=True, capture_output=True, timeout=45, check=True)
    if not re.fullmatch(r"[a-f0-9]{64}  -\n", completed.stdout):
        raise ValueError("unexpected sandbox result")
    return completed.stdout[:64]


def run(base: str, genesis: str, token_file: str, once: bool, interval: int):
    if os.geteuid() == 0:
        raise ValueError("run the worker as an ordinary user")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.path.endswith("/"):
        raise ValueError("base URL must be HTTPS and end with /")
    join(base, genesis)
    key = Path(token_file)
    mode = key.lstat()
    if (not stat.S_ISREG(mode.st_mode) or mode.st_uid != os.geteuid()
            or mode.st_mode & (stat.S_IRWXG | stat.S_IRWXO)):
        raise ValueError("worker token file must be regular and readable only by its owner")
    token = key.read_text().strip()
    if len(token) < 32:
        raise ValueError("invalid worker token")
    if not Path("/etc/subuid").exists():
        raise ValueError("rootless user mappings unavailable")
    capabilities = {"cpu_threads": os.cpu_count() or 1,
                    "memory_mb": min(2_000_000, os.sysconf("SC_PAGE_SIZE") *
                                     os.sysconf("SC_PHYS_PAGES") // 1048576)}
    while True:
        request(base, f"worker/{NODE_ID}/heartbeat", token, {"capabilities": capabilities})
        result = request(base, f"worker/{NODE_ID}/job", token)
        if "job_id" in result:
            if result.get("kind") != "sha256-fixed-v1":
                raise ValueError("unsupported job kind")
            digest = sandbox_job()
            receipt = request(base, f"worker/{NODE_ID}/result", token,
                              {"job_id": result["job_id"], "digest": digest})
            print(json.dumps(receipt), flush=True)
        if once:
            return
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://bokiloki.ddns.net/splitchain/")
    parser.add_argument("--genesis", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=int, default=15)
    args = parser.parse_args()
    if not 5 <= args.interval <= 30:
        parser.error("interval must be 5–30 seconds")
    run(args.base, args.genesis, args.token_file, args.once, args.interval)


if __name__ == "__main__":
    main()
