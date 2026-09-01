"""Certified three-round finality model for deterministic research tests."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass
from enum import Enum

from .identity import CertificateAuthority, NodeCertificate
from .model import ProtocolError, canonical_json


class FinalityRole(str, Enum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"


@dataclass(frozen=True)
class FinalityVote:
    observer: str
    role: FinalityRole
    branch_id: str
    candidate_digest: str
    round: int
    signature: str

    def unsigned(self) -> dict:
        value = asdict(self)
        value["role"] = self.role.value
        value.pop("signature")
        return value


class FinalitySigner:
    def __init__(
        self,
        certificate: NodeCertificate,
        role: FinalityRole,
        signing_key: str,
    ) -> None:
        self.certificate = certificate
        self.role = role
        self._key = signing_key.encode()

    def vote(self, branch_id: str, candidate_digest: str, round_number: int) -> FinalityVote:
        unsigned = {
            "branch_id": branch_id,
            "candidate_digest": candidate_digest,
            "observer": self.certificate.node_id,
            "role": self.role.value,
            "round": round_number,
        }
        signature = hmac.new(self._key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        return FinalityVote(signature=signature, role=self.role, **{
            key: value for key, value in unsigned.items() if key != "role"
        })

    @property
    def verification_key(self) -> bytes:
        return self._key


class ThreeRoundFinality:
    ROUNDS = 3

    def __init__(
        self,
        authority: CertificateAuthority,
        branch_id: str,
        candidate_digest: str,
        observers: dict[str, FinalityRole],
    ) -> None:
        if len(observers) < 3 or len(set(observers.values())) < 3:
            raise ProtocolError("finality requires Primary, Secondary, and Tertiary observers")
        self.authority = authority
        self.branch_id = branch_id
        self.candidate_digest = candidate_digest
        self.observers = observers
        self.current_round = 1
        self.votes: dict[int, dict[str, FinalityVote]] = {
            round_number: {} for round_number in range(1, self.ROUNDS + 1)
        }
        self.quarantined: set[str] = set()
        self.finalized = False

    @property
    def quorum_size(self) -> int:
        return (2 * len(self.observers) + 2) // 3

    def acknowledge(
        self,
        vote: FinalityVote,
        certificate: NodeCertificate,
        verification_key: bytes,
    ) -> bool:
        if self.finalized:
            raise ProtocolError("candidate is already final")
        if vote.round != self.current_round:
            raise ProtocolError("vote is not for the current finality round")
        if vote.branch_id != self.branch_id:
            raise ProtocolError("vote is for a different branch")
        if vote.observer != certificate.node_id:
            raise ProtocolError("vote observer does not match certificate")
        expected_role = self.observers.get(vote.observer)
        if expected_role is None or vote.role != expected_role:
            raise ProtocolError("observer is not assigned to this finality role")
        if vote.candidate_digest != self.candidate_digest:
            self.quarantined.add(vote.observer)
            raise ProtocolError("conflicting candidate vote")
        if not self.authority.verify(certificate, vote.round, "observer"):
            raise ProtocolError("observer certificate is invalid")
        expected = hmac.new(
            verification_key, canonical_json(vote.unsigned()), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, vote.signature):
            raise ProtocolError("invalid finality vote signature")
        existing = self.votes[vote.round].get(vote.observer)
        if existing:
            if existing == vote:
                return self.finalized
            self.quarantined.add(vote.observer)
            raise ProtocolError("observer equivocated in the same round")
        self.votes[vote.round][vote.observer] = vote
        if len(self.votes[vote.round]) >= self.quorum_size:
            if vote.round == self.ROUNDS:
                self.finalized = True
            else:
                self.current_round += 1
        return self.finalized

    def status(self) -> dict:
        return {
            "branch_id": self.branch_id,
            "candidate_digest": self.candidate_digest,
            "current_round": self.current_round,
            "finalized": self.finalized,
            "quorum_size": self.quorum_size,
            "votes": {
                str(round_number): sorted(round_votes)
                for round_number, round_votes in self.votes.items()
            },
            "quarantined": sorted(self.quarantined),
        }
