"""Local certificate model for research-node identity experiments."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass

from .model import ProtocolError, canonical_json


@dataclass(frozen=True)
class NodeCertificate:
    node_id: str
    public_key: str
    roles: tuple[str, ...]
    valid_from: int
    valid_until: int
    issuer: str
    signature: str

    def payload(self) -> dict:
        data = asdict(self)
        data.pop("signature")
        return data


class CertificateAuthority:
    """Deterministic HMAC CA for the local model; not production PKI."""

    def __init__(self, issuer: str, secret: str) -> None:
        self.issuer = issuer
        self._secret = secret.encode()

    def issue(
        self,
        node_id: str,
        public_key: str,
        roles: tuple[str, ...],
        valid_from: int,
        valid_until: int,
    ) -> NodeCertificate:
        if valid_until <= valid_from or not roles:
            raise ProtocolError("invalid certificate bounds or roles")
        payload = {
            "issuer": self.issuer,
            "node_id": node_id,
            "public_key": public_key,
            "roles": roles,
            "valid_from": valid_from,
            "valid_until": valid_until,
        }
        signature = hmac.new(
            self._secret, canonical_json(payload), hashlib.sha256
        ).hexdigest()
        return NodeCertificate(signature=signature, **payload)

    def verify(self, certificate: NodeCertificate, current_round: int, role: str) -> bool:
        expected = hmac.new(
            self._secret, canonical_json(certificate.payload()), hashlib.sha256
        ).hexdigest()
        return (
            certificate.issuer == self.issuer
            and certificate.valid_from <= current_round < certificate.valid_until
            and role in certificate.roles
            and hmac.compare_digest(expected, certificate.signature)
        )
