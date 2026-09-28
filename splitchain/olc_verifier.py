"""Separate-process Ed25519 attestation for the fixed OLC pilot job."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .model import canonical_json
from .olc_gateway import VERIFIER_IDS

EXPECTED = hashlib.sha256(b"OLC Worker01 test job\n").hexdigest()


def load_key(path: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("verifier key must be Ed25519")
    return key


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("verifier endpoint redirected")


def request(base: str, path: str, token: str, body: dict | None = None):
    content = json.dumps(body).encode() if body is not None else None
    call = Request(urljoin(base, path), data=content,
                   headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                   method="POST" if content is not None else "GET")
    with build_opener(NoRedirect).open(call, timeout=5) as response:
        payload = response.read(4097)
        if len(payload) > 4096:
            raise ValueError("verifier response too large")
        return json.loads(payload)


def verify_once(base: str, verifier_id: str, token: str, key: Ed25519PrivateKey):
    job = request(base, f"verifier/{verifier_id}/job", token)
    if "job_id" not in job:
        return None
    statement = {"job_id": job["job_id"], "node_id": job["node_id"],
                 "result_digest": job["result_digest"],
                 "accepted": job["result_digest"] == EXPECTED,
                 "verifier_id": verifier_id}
    signature = key.sign(canonical_json(statement)).hex()
    return request(base, f"verifier/{verifier_id}/attest", token,
                   {"statement": statement, "signature": signature})


def main():
    verifier_id = os.environ["OLC_VERIFIER_ID"]
    if verifier_id not in VERIFIER_IDS:
        raise ValueError("unknown verifier identity")
    base = os.environ.get("OLC_VERIFIER_BASE", "http://olc-gateway:8092/")
    address = urlsplit(base)
    if (not base.endswith("/") or address.scheme not in {"https", "http"}
            or address.scheme == "http" and address.netloc != "olc-gateway:8092"
            or not address.netloc or address.username or address.password
            or address.query or address.fragment):
        raise ValueError("verifier base URL must be HTTPS or the private gateway")
    token = Path(os.environ["OLC_VERIFIER_TOKEN_FILE"]).read_text().strip()
    key = load_key(os.environ["OLC_VERIFIER_KEY_FILE"])
    while True:
        try:
            result = verify_once(base, verifier_id, token, key)
            if result is not None:
                print(json.dumps(result), flush=True)
        except (OSError, URLError, ValueError) as exc:
            print(f"verifier request failed: {exc}", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
