from dataclasses import dataclass
from hashlib import sha256


@dataclass(frozen=True)
class Evidence:
    payload: bytes
    claimed_hash: str
    attestations: frozenset[str]


class TrueLies:
    def __init__(self, trusted_attestors: set[str], threshold: int = 2):
        self.trusted_attestors, self.threshold = trusted_attestors, threshold

    def verify(self, evidence: Evidence) -> bool:
        integrity = sha256(evidence.payload).hexdigest() == evidence.claimed_hash
        return integrity and len(evidence.attestations & self.trusted_attestors) >= self.threshold
