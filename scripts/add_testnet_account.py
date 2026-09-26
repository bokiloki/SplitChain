"""Provision a sandbox account and private credential without overwriting either."""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
from pathlib import Path


def add_account(registry: Path, actor: str, output: Path) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", actor) or actor.startswith("testnet_"):
        raise ValueError("account name must be lowercase and cannot use testnet_ prefix")
    if output.exists():
        raise FileExistsError("credential output already exists")
    accounts = json.loads(registry.read_text(encoding="utf-8"))
    if not isinstance(accounts, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) or len(v) < 32
        for k, v in accounts.items()
    ):
        raise ValueError("invalid account registry")
    if actor in accounts:
        raise ValueError("account already exists")
    secret = secrets.token_hex(32)
    accounts[actor] = secret
    temporary = registry.with_name(f".{registry.name}.new")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(temporary, flags, 0o600)
    try:
        os.fchown(fd, registry.stat().st_uid, registry.stat().st_gid)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(accounts, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with os.fdopen(os.open(output, flags, 0o600), "w", encoding="utf-8") as handle:
            handle.write(secret + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(temporary, registry)
        except OSError:
            output.unlink(missing_ok=True)
            raise
        directory_fd = os.open(registry.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: python3 scripts/add_testnet_account.py REGISTRY ACCOUNT OUTPUT_SECRET")
    add_account(Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3]))
    print(f"Account {sys.argv[2]} created. Deliver only its credential file to the tester.")


if __name__ == "__main__":
    main()
