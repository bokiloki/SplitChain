"""Separate-process Ed25519 attestation for the fixed OLC pilot job."""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .model import canonical_json

EXPECTED = hashlib.sha256(b"OLC Worker01 test job\n").hexdigest()


def load_key(path: str) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise TypeError("verifier key must be Ed25519")
    return key


def request(path: str, token: str, body: dict | None = None):
    content = json.dumps(body).encode() if body is not None else None
    call = Request("http://olc-gateway:8092" + path, data=content,
                   headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
                   method="POST" if content is not None else "GET")
    with urlopen(call, timeout=5) as response:
        return json.load(response)


def verify_once(token: str, key: Ed25519PrivateKey):
    job = request("/verifier/job", token)
    if "job_id" not in job:
        return None
    statement = {"job_id": job["job_id"], "node_id": job["node_id"],
                 "result_digest": job["result_digest"],
                 "accepted": job["result_digest"] == EXPECTED}
    signature = key.sign(canonical_json(statement)).hex()
    return request("/verifier/attest", token, {"statement": statement, "signature": signature})


def main():
    token = Path(os.environ["OLC_VERIFIER_TOKEN_FILE"]).read_text().strip()
    key = load_key(os.environ["OLC_VERIFIER_KEY_FILE"])
    while True:
        try:
            result = verify_once(token, key)
            if result is not None:
                print(json.dumps(result), flush=True)
        except (OSError, URLError, ValueError) as exc:
            print(f"verifier request failed: {exc}", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
