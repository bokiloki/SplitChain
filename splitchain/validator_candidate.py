"""Prepare independently signed candidate identities; does not grant a vote."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .model import GenesisConfig, ProtocolError, canonical_json

SCHEMA = "splitchain-validator-candidates/v1"
NAMES = ("colleague-primary", "colleague-secondary", "colleague-tertiary")


def prepare(genesis: GenesisConfig, output: Path) -> dict:
    """Generate three *candidate* identities on the colleague's machine only."""
    if any((parent / ".git").exists() for parent in (output.parent.resolve(), *output.parent.resolve().parents)):
        raise ProtocolError("private candidate keys must be created outside a Git checkout")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    nodes = []
    for name in NAMES:
        private = Ed25519PrivateKey.generate()
        path = output / f"{name}.pem"
        payload = {"schema": SCHEMA, "network_id": genesis.network_id,
                   "genesis_digest": genesis.digest(), "node_id": name,
                   "public_key": base64.b64encode(private.public_key().public_bytes(
                       serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode()}
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(pem)
            handle.flush()
            os.fsync(handle.fileno())
        nodes.append({**payload, "proof": base64.b64encode(private.sign(canonical_json(payload))).decode()})
    manifest = {"schema": SCHEMA, "network_id": genesis.network_id,
                "genesis_digest": genesis.digest(), "nodes": nodes}
    descriptor = os.open(output / "public-invite.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(canonical_json(manifest) + b"\n")
    return manifest


def verify(manifest: dict, genesis: GenesisConfig) -> None:
    if (not isinstance(manifest, dict) or set(manifest) != {"schema", "network_id", "genesis_digest", "nodes"}
            or manifest["schema"] != SCHEMA or manifest["network_id"] != genesis.network_id
            or manifest["genesis_digest"] != genesis.digest() or not isinstance(manifest["nodes"], list)
            or len(manifest["nodes"]) != 3):
        raise ProtocolError("candidate manifest does not match pinned genesis")
    if {node.get("node_id") for node in manifest["nodes"] if isinstance(node, dict)} != set(NAMES):
        raise ProtocolError("candidate identities are incomplete")
    for node in manifest["nodes"]:
        if (not isinstance(node, dict) or set(node) != {
                "schema", "network_id", "genesis_digest", "node_id", "public_key", "proof"}):
            raise ProtocolError("invalid candidate identity")
        unsigned = {key: value for key, value in node.items() if key != "proof"}
        if any(node.get(key) != manifest[key] for key in ("schema", "network_id", "genesis_digest")):
            raise ProtocolError("candidate uses a different network")
        try:
            public = base64.b64decode(node["public_key"], validate=True)
            signature = base64.b64decode(node["proof"], validate=True)
            Ed25519PublicKey.from_public_bytes(public).verify(signature, canonical_json(unsigned))
        except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
            raise ProtocolError("invalid candidate proof of possession") from exc


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare three independent candidate node keys (no voting rights)")
    parser.add_argument("command", choices=("prepare", "verify"))
    parser.add_argument("--genesis", type=Path, default=Path("configs/testnet-genesis.json"))
    parser.add_argument("--output", type=Path, help="new private output directory for prepare")
    parser.add_argument("--invite", type=Path, help="public invite manifest for verify")
    args = parser.parse_args()
    try:
        genesis = GenesisConfig.from_dict(json.loads(args.genesis.read_text()))
        if args.command == "prepare":
            if not args.output or args.invite:
                parser.error("prepare requires --output")
            prepare(genesis, args.output)
            print(f"Private candidate keys in {args.output}; share only public-invite.json.")
        else:
            if not args.invite or args.output:
                parser.error("verify requires --invite")
            verify(json.loads(args.invite.read_text()), genesis)
            print("Three candidate public keys and proofs match the pinned genesis.")
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(1, f"candidate setup stopped: {exc}\n")


if __name__ == "__main__":
    main()
