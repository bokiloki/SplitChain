"""Create a verifier key on its own host; print only the public key."""

import argparse
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("verifier_id", choices=("verifier-2", "verifier-3"))
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error("run as the ordinary verifier account")
    directory = Path.home() / ".config/olc"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    private_path = directory / f"{args.verifier_id}-private.pem"
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        output.write(private)
    public = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
    print(f"{args.verifier_id} public key: {public}")
    print(f"Private key saved locally at {private_path}; do not copy it to the gateway")


if __name__ == "__main__":
    main()
