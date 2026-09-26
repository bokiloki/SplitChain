"""Per-node Ed25519 signatures for the experimental cluster."""

from __future__ import annotations

import base64
import binascii
import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .failover import ROLE_ORDER
from .model import ProtocolError


class NodeKeyring:
    def __init__(
        self, role: str, private_key: Ed25519PrivateKey,
        public_keys: dict[str, Ed25519PublicKey],
    ) -> None:
        if role not in ROLE_ORDER or set(public_keys) != set(ROLE_ORDER):
            raise ProtocolError("node identity requires all three ordered roles")
        own = private_key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        configured = public_keys[role].public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        if own != configured:
            raise ProtocolError("node private key does not match its registered public key")
        self.role = role
        self._private = private_key
        self._public = public_keys.copy()

    @classmethod
    def from_files(
        cls, role: str, private_path: str | Path, registry_path: str | Path
    ) -> NodeKeyring:
        try:
            private = serialization.load_pem_private_key(Path(private_path).read_bytes(), None)
            document = json.loads(Path(registry_path).read_text(encoding="utf-8"))
            if not isinstance(private, Ed25519PrivateKey) or not isinstance(document, dict):
                raise TypeError("invalid key type or registry")
            public = {
                name: Ed25519PublicKey.from_public_bytes(
                    base64.b64decode(value, validate=True)
                ) for name, value in document.items()
            }
            return cls(role, private, public)
        except ProtocolError:
            raise
        except (OSError, ValueError, TypeError, binascii.Error) as exc:
            raise ProtocolError("invalid node identity material") from exc

    def sign(self, role: str, payload: bytes) -> str:
        if role != self.role:
            raise ProtocolError("node cannot sign for another role")
        return base64.b64encode(self._private.sign(payload)).decode("ascii")

    def verify(self, role: str, payload: bytes, signature: str) -> bool:
        public = self._public.get(role)
        if not public:
            return False
        try:
            public.verify(base64.b64decode(signature, validate=True), payload)
            return True
        except (InvalidSignature, ValueError, TypeError, binascii.Error):
            return False


def generate_node_key(role: str, destination: str | Path) -> str:
    """Create one private key with exclusive 0600 permissions; return its public key."""
    if role not in ROLE_ORDER:
        raise ProtocolError("unknown node role")
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = Path(destination)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(pem)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise ProtocolError("cannot create private key at the requested path") from exc
    raw = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")
