"""Add a distinct Ed25519 verifier credential to an existing OLC gateway."""

import json
import os
import secrets
import subprocess
import sys
from pathlib import Path


def create(path: Path, content: bytes, owner: int):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
    os.chown(path, owner, owner)
    os.chmod(path, 0o600)


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit("usage: sudo python3 scripts/init_olc_verifier.py /srv/splitchain-testnet/olc")
    directory = Path(sys.argv[1])
    credentials_path = directory / "credentials.json"
    original = credentials_path.read_bytes()
    credentials = json.loads(original)
    if "verifier" in credentials:
        raise SystemExit("verifier already provisioned; existing credentials retained")
    private = subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519"],
                             capture_output=True, check=True).stdout
    public = subprocess.run(["openssl", "pkey", "-pubout", "-outform", "DER"],
                            input=private, capture_output=True, check=True).stdout
    if len(public) != 44 or public[:12] != bytes.fromhex("302a300506032b6570032100"):
        raise ValueError("unexpected Ed25519 public key format")
    token = secrets.token_urlsafe(48)
    create(directory / "verifier-token", (token + "\n").encode(), 65532)
    create(directory / "verifier-private.pem", private, 65532)
    credentials["verifier"] = {"token": token, "public_key": public[12:].hex()}
    updated = json.dumps(credentials, separators=(",", ":")).encode() + b"\n"
    create(directory / "credentials.json.pre-verifier", original, 0)
    temp = directory / "credentials.json.new"
    create(temp, updated, 65532)
    os.replace(temp, credentials_path)
    print("Verifier key and token created; restart the OLC gateway to load the new public key")


if __name__ == "__main__":
    main()
