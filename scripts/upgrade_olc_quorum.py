"""Register two external verifier public keys without receiving private keys."""

import argparse
import json
import os
import secrets
from pathlib import Path


def create(path: Path, content: bytes, owner: int):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())
    os.chown(path, owner, owner)
    os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--verifier-2-public", required=True)
    parser.add_argument("--verifier-3-public", required=True)
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("run as root on the testnet server")
    for public in (args.verifier_2_public, args.verifier_3_public):
        if len(public) != 64 or any(char not in "0123456789abcdef" for char in public):
            parser.error("public keys must be 32-byte lowercase hexadecimal values")
    credential_file = args.directory / "credentials.json"
    original = credential_file.read_bytes()
    credentials = json.loads(original)
    if "verifiers" in credentials:
        parser.error("quorum already configured; credentials retained")
    old = credentials["verifier"]
    if len({old["public_key"], args.verifier_2_public, args.verifier_3_public}) != 3:
        parser.error("all three verifier public keys must be distinct")
    if len({old["token"], credentials["operator"], *credentials["workers"].values()}) != (
            2 + len(credentials["workers"])):
        parser.error("existing tokens must be distinct")
    second, third = secrets.token_urlsafe(48), secrets.token_urlsafe(48)
    create(args.directory / "verifier-2-token", (second + "\n").encode(), 0)
    create(args.directory / "verifier-3-token", (third + "\n").encode(), 0)
    credentials["verifiers"] = {
        "verifier-1": old,
        "verifier-2": {"token": second, "public_key": args.verifier_2_public},
        "verifier-3": {"token": third, "public_key": args.verifier_3_public},
    }
    create(args.directory / "credentials.json.pre-quorum", original, 0)
    temp = args.directory / "credentials.json.quorum-new"
    create(temp, json.dumps(credentials, separators=(",", ":")).encode() + b"\n", 65532)
    os.replace(temp, credential_file)
    print("Three-key verifier roster registered; tokens were not printed")


if __name__ == "__main__":
    main()
