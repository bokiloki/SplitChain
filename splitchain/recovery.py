"""Failure-claim and deterministic role-takeover research model."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass
from enum import Enum

from .finality import FinalityRole
from .identity import CertificateAuthority, NodeCertificate
from .model import ProtocolError, canonical_json


class FailureKind(str, Enum):
    NO_REVEAL = "no_reveal"
    INVALID_REVEAL = "invalid_reveal"
    INVALID_BLOCK = "invalid_block"
    EQUIVOCATION = "equivocation"
    CENSORSHIP = "censorship"
    MERGE_FRAUD = "merge_fraud"


@dataclass(frozen=True)
class FailureClaim:
    reporter: str
    accused_role: FinalityRole
    branch_id: str
    protocol_round: int
    kind: FailureKind
    evidence_digest: str
    signature: str

    def unsigned(self) -> dict:
        value = asdict(self)
        value["accused_role"] = self.accused_role.value
        value["kind"] = self.kind.value
        value.pop("signature")
        return value


@dataclass(frozen=True)
class Counterproof:
    actor: str
    accused_role: FinalityRole
    branch_id: str
    protocol_round: int
    evidence_digest: str
    signature: str

    def unsigned(self) -> dict:
        value = asdict(self)
        value["accused_role"] = self.accused_role.value
        value.pop("signature")
        return value


class RecoverySigner:
    def __init__(self, certificate: NodeCertificate, signing_key: str) -> None:
        self.certificate = certificate
        self._key = signing_key.encode()

    @property
    def verification_key(self) -> bytes:
        return self._key

    def claim(
        self,
        accused_role: FinalityRole,
        branch_id: str,
        protocol_round: int,
        kind: FailureKind,
        evidence_digest: str,
    ) -> FailureClaim:
        unsigned = {
            "accused_role": accused_role.value,
            "branch_id": branch_id,
            "evidence_digest": evidence_digest,
            "kind": kind.value,
            "protocol_round": protocol_round,
            "reporter": self.certificate.node_id,
        }
        signature = hmac.new(self._key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        return FailureClaim(
            reporter=unsigned["reporter"],
            accused_role=accused_role,
            branch_id=branch_id,
            protocol_round=protocol_round,
            kind=kind,
            evidence_digest=evidence_digest,
            signature=signature,
        )

    def counterproof(
        self,
        accused_role: FinalityRole,
        branch_id: str,
        protocol_round: int,
        evidence_digest: str,
    ) -> Counterproof:
        unsigned = {
            "accused_role": accused_role.value,
            "actor": self.certificate.node_id,
            "branch_id": branch_id,
            "evidence_digest": evidence_digest,
            "protocol_round": protocol_round,
        }
        signature = hmac.new(self._key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        return Counterproof(
            actor=unsigned["actor"],
            accused_role=accused_role,
            branch_id=branch_id,
            protocol_round=protocol_round,
            evidence_digest=evidence_digest,
            signature=signature,
        )


class RecoveryCoordinator:
    """Models proof-gated takeover; it deliberately performs no economic slashing."""

    ROLE_ORDER = (
        FinalityRole.PRIMARY,
        FinalityRole.SECONDARY,
        FinalityRole.TERTIARY,
    )

    def __init__(
        self,
        authority: CertificateAuthority,
        branch_id: str,
        role_nodes: dict[FinalityRole, str],
        observer_count: int = 3,
        counterproof_window: int = 3,
    ) -> None:
        if observer_count < 3 or counterproof_window < 1:
            raise ProtocolError("recovery requires three observers and a positive window")
        if set(role_nodes) != set(self.ROLE_ORDER):
            raise ProtocolError("recovery requires all ordered finality roles")
        self.authority = authority
        self.branch_id = branch_id
        self.role_nodes = role_nodes
        self.observer_count = observer_count
        self.counterproof_window = counterproof_window
        self.active_role = FinalityRole.PRIMARY
        self.claims: dict[str, FailureClaim] = {}
        self.pending_deadline: int | None = None
        self.pending_round: int | None = None
        self.quarantined: set[str] = set()
        self.disputed: list[dict] = []
        self.aborted = False

    @property
    def quorum_size(self) -> int:
        return (2 * self.observer_count + 2) // 3

    def submit_claim(
        self,
        claim: FailureClaim,
        certificate: NodeCertificate,
        verification_key: bytes,
    ) -> bool:
        if self.aborted or self.pending_deadline is not None:
            raise ProtocolError("recovery is not accepting failure claims")
        if claim.branch_id != self.branch_id or claim.accused_role != self.active_role:
            raise ProtocolError("failure claim targets the wrong branch or role")
        self._verify(certificate, claim.reporter, claim.protocol_round, claim, verification_key)
        existing = self.claims.get(claim.reporter)
        if existing:
            if existing == claim:
                return False
            self.quarantined.add(claim.reporter)
            raise ProtocolError("reporter submitted conflicting failure evidence")
        if self.claims and claim.protocol_round != next(iter(self.claims.values())).protocol_round:
            raise ProtocolError("failure claims are from different protocol rounds")
        self.claims[claim.reporter] = claim
        if len(self.claims) >= self.quorum_size:
            self.pending_round = claim.protocol_round
            self.pending_deadline = claim.protocol_round + self.counterproof_window
            return True
        return False

    def submit_counterproof(
        self,
        proof: Counterproof,
        certificate: NodeCertificate,
        verification_key: bytes,
        current_round: int,
    ) -> None:
        if self.pending_deadline is None or self.pending_round is None:
            raise ProtocolError("no failure challenge is pending")
        if current_round > self.pending_deadline:
            raise ProtocolError("counterproof window is closed")
        expected_actor = self.role_nodes[self.active_role]
        if proof.actor != expected_actor or proof.accused_role != self.active_role:
            raise ProtocolError("counterproof is not from the accused role holder")
        if proof.branch_id != self.branch_id or proof.protocol_round != self.pending_round:
            raise ProtocolError("counterproof targets the wrong branch or round")
        self._verify(certificate, proof.actor, current_round, proof, verification_key)
        self.disputed.append({
            "role": self.active_role.value,
            "round": self.pending_round,
            "counterproof": proof.evidence_digest,
        })
        self.claims.clear()
        self.pending_deadline = None
        self.pending_round = None

    def advance(self, current_round: int) -> FinalityRole | None:
        if self.pending_deadline is None:
            raise ProtocolError("no proof-gated takeover is pending")
        if current_round <= self.pending_deadline:
            raise ProtocolError("counterproof window remains open")
        index = self.ROLE_ORDER.index(self.active_role)
        self.claims.clear()
        self.pending_deadline = None
        self.pending_round = None
        if index + 1 == len(self.ROLE_ORDER):
            self.aborted = True
            return None
        self.active_role = self.ROLE_ORDER[index + 1]
        return self.active_role

    def _verify(
        self,
        certificate: NodeCertificate,
        actor: str,
        current_round: int,
        message: FailureClaim | Counterproof,
        verification_key: bytes,
    ) -> None:
        if certificate.node_id != actor:
            raise ProtocolError("proof actor does not match certificate")
        if not self.authority.verify(certificate, current_round, "observer"):
            raise ProtocolError("proof certificate is invalid")
        expected = hmac.new(
            verification_key, canonical_json(message.unsigned()), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, message.signature):
            raise ProtocolError("invalid failure-proof signature")
