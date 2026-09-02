"""Authenticated mutation envelopes for the three-node reference cluster."""

from __future__ import annotations

import hashlib
import hmac

from .model import ProtocolError, canonical_json


class ReplicationAuthenticator:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ProtocolError("cluster replication secret must contain at least 32 characters")
        self._secret = secret.encode()

    def sign(self, leader: str, nonce: int, mutation: dict) -> dict:
        if nonce < 1:
            raise ProtocolError("replication nonce must be positive")
        payload = {"leader": leader, "mutation": mutation, "nonce": nonce}
        return {
            **payload,
            "signature": hmac.new(
                self._secret, canonical_json(payload), hashlib.sha256
            ).hexdigest(),
        }

    def verify(self, envelope: dict, last_nonce: int) -> tuple[str, int, dict]:
        try:
            leader = str(envelope["leader"])
            nonce = int(envelope["nonce"])
            mutation = envelope["mutation"]
            signature = str(envelope["signature"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid replication envelope") from exc
        payload = {"leader": leader, "mutation": mutation, "nonce": nonce}
        expected = hmac.new(
            self._secret, canonical_json(payload), hashlib.sha256
        ).hexdigest()
        if leader != "primary" or nonce <= last_nonce or not hmac.compare_digest(expected, signature):
            raise ProtocolError("invalid or replayed replication envelope")
        if not isinstance(mutation, dict) or mutation.get("method") not in {
            "offer", "accept", "commit", "cancel", "advance"
        }:
            raise ProtocolError("replication envelope contains an invalid mutation")
        return leader, nonce, mutation
