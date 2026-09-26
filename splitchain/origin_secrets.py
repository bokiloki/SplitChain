"""Originator-only encrypted reveal material; never included in consensus snapshots."""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from .model import ProtocolError, canonical_json


class OriginBetSecrets:
    def __init__(self, directory: str | Path, node_id: str, key: bytes) -> None:
        if not node_id or not isinstance(key, bytes) or len(key) != 32:
            raise ProtocolError("origin secrets require a node identity and 32-byte private key")
        self.directory = Path(directory)
        self.node_id = node_id
        self._cipher = ChaCha20Poly1305(key)

    def _file(self, position: int) -> Path:
        if type(position) is not int or position < 0:
            raise ProtocolError("invalid origin bet position")
        return self.directory / f"bet-{position}.sealed"

    def save(self, position: int, epoch_digest: str, secret: str) -> None:
        if not epoch_digest or not isinstance(secret, str) or len(secret) < 32:
            raise ProtocolError("invalid origin reveal secret")
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.directory.stat().st_mode & 0o077:
            raise ProtocolError("origin secret directory must be private")
        path = self._file(position)
        nonce = os.urandom(12)
        aad = canonical_json({"node": self.node_id, "position": position,
                              "epoch_digest": epoch_digest})
        encrypted = self._cipher.encrypt(nonce, secret.encode("utf-8"), aad)
        encoded = json.dumps({"nonce": nonce.hex(), "ciphertext": encrypted.hex()},
                             sort_keys=True).encode()
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise ProtocolError("origin bet secret already exists") from exc
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        directory_fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def read(self, position: int, epoch_digest: str) -> str:
        try:
            document = json.loads(self._file(position).read_text())
            aad = canonical_json({"node": self.node_id, "position": position,
                                  "epoch_digest": epoch_digest})
            return self._cipher.decrypt(
                bytes.fromhex(document["nonce"]), bytes.fromhex(document["ciphertext"]), aad,
            ).decode("utf-8")
        except (OSError, KeyError, ValueError, UnicodeError, InvalidTag) as exc:
            raise ProtocolError("origin reveal secret is unavailable or invalid") from exc
