"""TrueLies proof and observer-quorum layer."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ServiceEvent:
    event_id: str
    service: str
    actor: str
    payload: dict

    @classmethod
    def create(cls, service: str, actor: str, payload: dict) -> ServiceEvent:
        canonical = json.dumps(
            {"service": service, "actor": actor, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls(hashlib.sha256(canonical.encode()).hexdigest()[:20], service, actor, payload)


@dataclass(frozen=True)
class Attestation:
    observer: str
    event_id: str
    accepted: bool
    signature: str


class Observer:
    """Deterministic HMAC observer used only by the local research ecosystem."""

    def __init__(self, name: str, secret: str) -> None:
        self.name = name
        self._secret = secret.encode()

    def attest(self, event: ServiceEvent, accepted: bool = True) -> Attestation:
        message = f"{self.name}:{event.event_id}:{int(accepted)}".encode()
        signature = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return Attestation(self.name, event.event_id, accepted, signature)

    def verify(self, attestation: Attestation) -> bool:
        message = (
            f"{attestation.observer}:{attestation.event_id}:{int(attestation.accepted)}".encode()
        )
        expected = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return attestation.observer == self.name and hmac.compare_digest(
            expected, attestation.signature
        )


class TrueLies:
    """Three-observer proof registry requiring a 2/3 positive quorum."""

    def __init__(self, observers: list[Observer] | None = None) -> None:
        self.observers = observers or [
            Observer("primary", "local-primary-key"),
            Observer("secondary", "local-secondary-key"),
            Observer("tertiary", "local-tertiary-key"),
        ]
        if len(self.observers) < 3:
            raise ValueError("TrueLies requires at least three observers")
        self.proofs: dict[str, dict] = {}
        self.reputation: dict[str, int] = {}

    def prove(self, event: ServiceEvent, votes: list[bool] | None = None) -> dict:
        votes = votes or [True] * len(self.observers)
        attestations = [
            observer.attest(event, votes[index])
            for index, observer in enumerate(self.observers)
        ]
        valid = [
            attestation
            for observer, attestation in zip(self.observers, attestations, strict=True)
            if observer.verify(attestation)
        ]
        approvals = sum(item.accepted for item in valid)
        quorum = approvals * 3 >= len(self.observers) * 2
        proof = {
            "event": asdict(event),
            "attestations": [asdict(item) for item in attestations],
            "approvals": approvals,
            "quorum": quorum,
        }
        self.proofs[event.event_id] = proof
        self.reputation[event.actor] = self.reputation.get(event.actor, 0) + (1 if quorum else -1)
        return proof

