"""Generate an isolated six-container sandbox and mTLS relay rehearsal."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.x509.oid import NameOID

from .membership import StakeMembership
from .model import GenesisConfig, Ledger, ProtocolError
from .stake_votes import StakeVoteBook

NODES = ("primary", "secondary", "tertiary", "colleague-primary",
         "colleague-secondary", "colleague-tertiary")


def _write(path: Path, content: bytes, mode: int = 0o600) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        os.fchmod(stream.fileno(), mode)


def bootstrap(genesis_path: Path, output: Path, container_uid: int | None = None) -> dict:
    """Create a new disposable deployment; refuse existing destinations."""
    if output.exists():
        raise ProtocolError("sandbox output exists; never overwrite validator keys or history")
    genesis = GenesisConfig.from_dict(json.loads(genesis_path.read_text()))
    reserve = "testnet_locked_reserve"
    backing = Ledger(genesis=genesis).balances[reserve]
    first = backing // 3
    allocations = dict(zip(NODES, (first, first, backing - 2 * first, 0, 0, 0), strict=True))
    membership = StakeMembership.from_locked_reserve(
        Ledger(genesis=genesis), reserve, 0, tuple(sorted(allocations.items())),
    )
    keys = {name: Ed25519PrivateKey.generate() for name in NODES}
    public = {name: key.public_key() for name, key in keys.items()}
    book = StakeVoteBook(membership, genesis.network_id, genesis.digest(), public)
    output.mkdir(mode=0o700, parents=True)
    shared = output / "shared"
    shared.mkdir(mode=0o755)
    _write(shared / "genesis.json", genesis_path.read_bytes(), 0o644)
    manifest = {
        "schema": "splitchain/stake-manifest/v1", "network_id": genesis.network_id,
        "genesis_digest": genesis.digest(), "reserve_account": reserve, "epoch": 0,
        "allocations": allocations,
        "public_keys": {name: base64.b64encode(public[name].public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )).decode() for name in NODES},
    }
    _write(shared / "manifest.json", json.dumps(manifest, sort_keys=True).encode(), 0o644)
    authority = generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)  # noqa: UP017 (Python 3.10 compatibility)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SplitChain isolated rehearsal")])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(authority.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(minutes=5))
          .not_valid_after(now + timedelta(days=2))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(authority, hashes.SHA256()))
    _write(shared / "ca.pem", ca.public_bytes(serialization.Encoding.PEM), 0o644)
    _write(output / "ca.key", authority.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    registry = []
    services: dict = {}
    peers = [f"{other}=wss://relay-{other}:8765" for other in NODES]
    for name in NODES:
        node = output / name
        node.mkdir(mode=0o700)
        (node / "state").mkdir(mode=0o700)
        (node / "socket").mkdir(mode=0o700)
        _write(node / "signing.pem", keys[name].private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        tls_key = generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"relay-{name}")])
        certificate = (x509.CertificateBuilder().subject_name(subject)
                       .issuer_name(ca_name).public_key(tls_key.public_key())
                       .serial_number(x509.random_serial_number())
                       .not_valid_before(now - timedelta(minutes=5))
                       .not_valid_after(now + timedelta(days=2))
                       .add_extension(x509.SubjectAlternativeName([
                           x509.DNSName(f"relay-{name}"),
                       ]), critical=False)
                       .add_extension(x509.ExtendedKeyUsage([
                           x509.oid.ExtendedKeyUsageOID.SERVER_AUTH,
                           x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH,
                       ]), critical=False).sign(authority, hashes.SHA256()))
        der = certificate.public_bytes(serialization.Encoding.DER)
        _write(node / "tls.pem", certificate.public_bytes(serialization.Encoding.PEM))
        _write(node / "tls.key", tls_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))
        registry.append({"node_id": name, "certificate_sha256": hashlib.sha256(der).hexdigest(),
                         "roles": ["overlord"]})
        common = {"build": {"context": str(Path(__file__).resolve().parents[1]),
                             "dockerfile": "Dockerfile"},
                  "image": "splitchain-sandbox-rehearsal:local", "read_only": True,
                  "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
                  "pids_limit": 64, "mem_limit": "256m", "restart": "unless-stopped"}
        services[f"sandbox-{name}"] = {
            **common, "entrypoint": ["python", "-m", "splitchain.sandbox_daemon"],
            "command": ["--socket", "/socket/consensus.sock", "--state", "/state/consensus.json",
                        "--genesis", "/shared/genesis.json", "--manifest", "/shared/manifest.json",
                        "--expected-epoch-digest", book.epoch_digest,
                        "--node-id", name, "--node-key", "/node/signing.pem"],
            "network_mode": "none",
            "volumes": [f"./{name}/state:/state", f"./{name}/socket:/socket",
                        "./shared:/shared:ro", f"./{name}:/node:ro"],
        }
        command = ["--node-id", name, "--socket", "/socket/consensus.sock",
                   "--registry", "/shared/registry.json", "--tls-cert", "/node/tls.pem",
                   "--tls-key", "/node/tls.key", "--tls-ca", "/shared/ca.pem"]
        for peer in peers:
            if not peer.startswith(f"{name}="):
                command += ["--peer", peer]
        services[f"relay-{name}"] = {
            **common, "entrypoint": ["python", "-m", "splitchain.bet_relay"],
            "command": command,
            "depends_on": [f"sandbox-{name}"], "networks": ["isolated"],
            "volumes": [f"./{name}/socket:/socket", "./shared:/shared:ro",
                        f"./{name}:/node:ro"],
        }
    _write(shared / "registry.json", json.dumps({"peers": registry}).encode(), 0o644)
    _write(output / "compose.json", json.dumps({"name": "splitchain-sandbox-rehearsal",
        "services": services, "networks": {"isolated": {"internal": True}}}, indent=2).encode(), 0o600)
    _write(output / "epoch-digest.txt", (book.epoch_digest + "\n").encode())
    if container_uid is not None:
        if os.geteuid() != 0:
            raise ProtocolError("--container-uid requires running bootstrap as root")
        for name in NODES:
            for path in (output / name, *(output / name).rglob("*")):
                os.chown(path, container_uid, container_uid)
    return {"nodes": len(NODES), "epoch_digest": book.epoch_digest,
            "output": str(output), "quorum_stake": membership.quorum_stake}


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate isolated six-validator Docker rehearsal")
    parser.add_argument("--genesis", type=Path, default=Path("configs/testnet-genesis.json"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--container-uid", type=int)
    args = parser.parse_args()
    print(json.dumps(bootstrap(args.genesis, args.output, args.container_uid), indent=2))


if __name__ == "__main__":
    main()
