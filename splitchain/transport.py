"""TLS 1.3 mutual-authentication policy for SplitChain reference networking."""

from __future__ import annotations

import hashlib
import json
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from .model import ProtocolError

ALLOWED_PEER_ROLES = frozenset({"primary", "secondary", "tertiary", "overlord", "client"})


@dataclass(frozen=True)
class PeerIdentity:
    node_id: str
    certificate_sha256: str
    roles: tuple[str, ...]

    METHOD_ROLES: ClassVar[dict[str, frozenset[str]]] = {
        "status": ALLOWED_PEER_ROLES,
        "ecosystem.demo": ALLOWED_PEER_ROLES,
        "cluster.status": ALLOWED_PEER_ROLES,
        "cluster.sync": frozenset({"primary", "overlord"}),
        "offer": frozenset({"client"}),
        "accept": frozenset({"client"}),
        "commit": frozenset({"client"}),
        "cancel": frozenset({"client"}),
        "advance": frozenset({"primary", "secondary", "tertiary", "overlord"}),
    }

    def authorize(self, method: str | None) -> None:
        permitted = self.METHOD_ROLES.get(method or "", frozenset())
        if not permitted.intersection(self.roles):
            raise ProtocolError(
                f"peer {self.node_id} is not authorized for method {method or '<missing>'}"
            )


class PeerRegistry:
    """Bind TLS certificate fingerprints to explicit SplitChain identities."""

    def __init__(self, peers: tuple[PeerIdentity, ...]) -> None:
        if not peers:
            raise ProtocolError("peer certificate registry cannot be empty")
        fingerprints: dict[str, PeerIdentity] = {}
        node_ids: set[str] = set()
        for peer in peers:
            fingerprint = peer.certificate_sha256.lower()
            if len(fingerprint) != 64 or any(char not in "0123456789abcdef" for char in fingerprint):
                raise ProtocolError("peer certificate fingerprint must be sha256 hex")
            if not peer.node_id or peer.node_id in node_ids or fingerprint in fingerprints:
                raise ProtocolError("peer certificate registry contains a duplicate identity")
            if not peer.roles or not set(peer.roles).issubset(ALLOWED_PEER_ROLES):
                raise ProtocolError("peer certificate registry contains an invalid role")
            normalized = PeerIdentity(peer.node_id, fingerprint, tuple(sorted(set(peer.roles))))
            fingerprints[fingerprint] = normalized
            node_ids.add(peer.node_id)
        self._fingerprints = fingerprints

    @classmethod
    def from_path(cls, path: str | Path) -> PeerRegistry:
        try:
            document = json.loads(Path(path).read_text())
            values = document["peers"]
            peers = tuple(
                PeerIdentity(value["node_id"], value["certificate_sha256"], tuple(value["roles"]))
                for value in values
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ProtocolError("invalid peer certificate registry") from exc
        return cls(peers)

    def verify_der(self, certificate: bytes | None) -> PeerIdentity:
        if not certificate:
            raise ProtocolError("peer did not present a TLS certificate")
        fingerprint = hashlib.sha256(certificate).hexdigest()
        try:
            return self._fingerprints[fingerprint]
        except KeyError as exc:
            raise ProtocolError("peer TLS certificate is not authorized") from exc


@dataclass(frozen=True)
class TLSMaterial:
    certificate: Path
    private_key: Path
    certificate_authority: Path

    @classmethod
    def from_values(
        cls,
        certificate: str | Path | None,
        private_key: str | Path | None,
        certificate_authority: str | Path | None,
    ) -> TLSMaterial | None:
        values = (certificate, private_key, certificate_authority)
        if not any(values):
            return None
        if not all(values):
            raise ProtocolError("TLS requires certificate, private key and certificate authority")
        material = cls(*(Path(value) for value in values if value is not None))
        missing = [str(path) for path in material.paths() if not path.is_file()]
        if missing:
            raise ProtocolError(f"TLS material does not exist: {', '.join(missing)}")
        return material

    def paths(self) -> tuple[Path, Path, Path]:
        return self.certificate, self.private_key, self.certificate_authority

    def server_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        self._configure(context)
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=str(self.certificate_authority),
        )
        self._configure(context, load_authority=False)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        return context

    def _configure(self, context: ssl.SSLContext, *, load_authority: bool = True) -> None:
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        if load_authority:
            context.load_verify_locations(cafile=str(self.certificate_authority))
        context.load_cert_chain(str(self.certificate), str(self.private_key))
