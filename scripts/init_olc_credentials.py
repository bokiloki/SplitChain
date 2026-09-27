"""Create private OLC pilot credentials once on the testnet server."""

import json
import os
import secrets
import sys
from pathlib import Path


def main():
    if os.geteuid() != 0 or len(sys.argv) != 2:
        raise SystemExit("usage: sudo python3 scripts/init_olc_credentials.py /srv/splitchain-testnet/olc")
    directory = Path(sys.argv[1])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    credentials = {"workers": {"olc-worker-001": secrets.token_urlsafe(48)},
                   "operator": secrets.token_urlsafe(48)}
    outputs = {
        "credentials.json": (json.dumps(credentials, separators=(",", ":")) + "\n", 65532),
        "worker-token": (credentials["workers"]["olc-worker-001"] + "\n", 0),
        "operator-token": (credentials["operator"] + "\n", 0),
    }
    for name, (content, owner) in outputs.items():
        path = directory / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(content)
        os.chown(path, owner, owner)
        os.chmod(path, 0o600)
    # The gateway runs as UID 65532 and needs traverse access to this directory.
    os.chmod(directory, 0o711)
    print("OLC credentials created; tokens were not printed")


if __name__ == "__main__":
    main()
