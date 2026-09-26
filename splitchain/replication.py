"""Authenticated mutation envelopes for the three-node reference cluster."""

from __future__ import annotations

import hashlib
import hmac
from typing import TYPE_CHECKING

from .model import ProtocolError, canonical_json

if TYPE_CHECKING:
    from .node_identity import NodeKeyring


class ReplicationAuthenticator:
    def __init__(self, secret: str | None = None, *, keyring: NodeKeyring | None = None) -> None:
        if (secret is None) == (keyring is None):
            raise ProtocolError("choose one replication identity scheme")
        if secret is not None and len(secret) < 32:
            raise ProtocolError("cluster replication secret must contain at least 32 characters")
        self._secret = secret.encode() if secret else None
        self._keyring = keyring

    def sign(self, leader: str, nonce: int, mutation: dict) -> dict:
        if nonce < 1:
            raise ProtocolError("replication nonce must be positive")
        payload = {"leader": leader, "mutation": mutation, "nonce": nonce}
        signature = (
            self._keyring.sign(leader, canonical_json(payload))
            if self._keyring else hmac.new(
                self._secret, canonical_json(payload), hashlib.sha256
            ).hexdigest()
        )
        return {**payload, "signature": signature}

    def verify(
        self, envelope: dict, last_nonce: int, expected_leader: str = "primary"
    ) -> tuple[str, int, dict]:
        try:
            leader = str(envelope["leader"])
            nonce = int(envelope["nonce"])
            mutation = envelope["mutation"]
            signature = str(envelope["signature"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid replication envelope") from exc
        payload = {"leader": leader, "mutation": mutation, "nonce": nonce}
        valid = (
            self._keyring.verify(leader, canonical_json(payload), signature)
            if self._keyring else hmac.compare_digest(
                hmac.new(self._secret, canonical_json(payload), hashlib.sha256).hexdigest(),
                signature,
            )
        )
        if leader != expected_leader or nonce <= last_nonce or not valid:
            raise ProtocolError("invalid or replayed replication envelope")
        if not isinstance(mutation, dict) or mutation.get("method") not in {
            "offer", "accept", "commit", "cancel", "advance"
        }:
            raise ProtocolError("replication envelope contains an invalid mutation")
        return leader, nonce, mutation
