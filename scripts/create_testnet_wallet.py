"""Create one private recipient wallet in the operator account registry."""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python3 scripts/create_testnet_wallet.py ACCOUNTS_JSON WALLET_ID")
    path = Path(sys.argv[1])
    wallet_id = sys.argv[2]
    if not wallet_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in wallet_id):
        raise SystemExit("WALLET_ID may contain only letters, numbers, underscore, and hyphen")
    if not path.exists():
        raise SystemExit(f"account registry does not exist: {path}")
    original = path.stat()
    accounts = json.loads(path.read_text(encoding="utf-8"))
    if wallet_id in accounts:
        raise SystemExit(f"wallet already exists: {wallet_id}")
    secret = secrets.token_hex(32)
    accounts[wallet_id] = secret
    temporary = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.fchown(fd, original.st_uid, original.st_gid)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(accounts, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(f"wallet_id={wallet_id}")
    print(f"secret={secret}")
    print("Deliver this secret privately to the wallet owner; it cannot be recovered from GitHub.")


if __name__ == "__main__":
    main()
