"""Create a private, operator-curated sandbox account registry exactly once."""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python3 scripts/init_testnet_accounts.py OUTPUT_JSON")
    path = Path(sys.argv[1])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    accounts = {
        actor: secrets.token_hex(32)
        for actor in ("testnet_faucet", "testnet_operator", "alice", "bob")
    }
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(accounts, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    print(f"Created {path} with four private sandbox accounts; do not publish this file.")


if __name__ == "__main__":
    main()
