"""Term-based, quorum-certified leadership failover safety core."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from .model import ProtocolError, canonical_json

if TYPE_CHECKING:
    from .node_identity import NodeKeyring

ROLE_ORDER = ("primary", "secondary", "tertiary")


@dataclass(frozen=True)
class FailureVote:
    voter: str
    term: int
    accused: str
    candidate: str
    observed_tick: int
    committed_nonce: int
    signature: str

    def unsigned(self) -> dict:
        value = asdict(self)
        value.pop("signature")
        return value


@dataclass(frozen=True)
class LeadershipCertificate:
    term: int
    leader: str
    committed_nonce: int
    votes: tuple[FailureVote, ...]
    digest: str

    @property
    def voters(self) -> tuple[str, ...]:
        return tuple(vote.voter for vote in self.votes)


class FailoverAuthority:
    def __init__(
        self, node_keys: dict[str, str] | None = None, *, keyring: NodeKeyring | None = None
    ) -> None:
        if (node_keys is None) == (keyring is None):
            raise ProtocolError("choose one failover identity scheme")
        if node_keys is not None and (
            set(node_keys) != set(ROLE_ORDER) or any(len(key) < 32 for key in node_keys.values())
        ):
            raise ProtocolError("failover requires a strong key for every ordered role")
        self._keys = {node: key.encode() for node, key in (node_keys or {}).items()}
        self._keyring = keyring

    def _sign(self, role: str, payload: dict) -> str:
        if self._keyring:
            return self._keyring.sign(role, canonical_json(payload))
        try:
            key = self._keys[role]
        except KeyError as exc:
            raise ProtocolError("unknown failover voter") from exc
        return hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()

    def _verify(self, role: str, payload: dict, signature: str) -> bool:
        if self._keyring:
            return self._keyring.verify(role, canonical_json(payload), signature)
        key = self._keys.get(role)
        if not key:
            return False
        expected = hmac.new(key, canonical_json(payload), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def vote(
        self,
        voter: str,
        term: int,
        accused: str,
        candidate: str,
        observed_tick: int,
        committed_nonce: int,
    ) -> FailureVote:
        unsigned = {
            "accused": accused,
            "candidate": candidate,
            "committed_nonce": committed_nonce,
            "observed_tick": observed_tick,
            "term": term,
            "voter": voter,
        }
        signature = self._sign(voter, unsigned)
        return FailureVote(signature=signature, **unsigned)

    def verify(self, vote: FailureVote) -> bool:
        return self._verify(vote.voter, vote.unsigned(), vote.signature)

    def sign_heartbeat(self, leader: str, term: int, tick: int, nonce: int) -> dict:
        payload = {"leader": leader, "term": term, "tick": tick, "nonce": nonce}
        return {
            **payload,
            "signature": self._sign(leader, payload),
        }

    def verify_heartbeat(self, envelope: dict) -> bool:
        try:
            leader = envelope["leader"]
            payload = {key: envelope[key] for key in ("leader", "term", "tick", "nonce")}
            signature = envelope["signature"]
            return self._verify(leader, payload, signature)
        except (KeyError, TypeError, ValueError):
            return False


class LeadershipState:
    QUORUM = 2

    def __init__(self, authority: FailoverAuthority, timeout_ticks: int = 3) -> None:
        if timeout_ticks < 2:
            raise ProtocolError("leadership timeout must be at least two ticks")
        self.authority = authority
        self.timeout_ticks = timeout_ticks
        self.term = 0
        self.leader = "primary"
        self.last_heartbeat_tick = 0
        self.committed_nonce = 0
        self.votes: dict[str, FailureVote] = {}
        self.certificates: list[LeadershipCertificate] = []

    def heartbeat(self, leader: str, term: int, tick: int, committed_nonce: int) -> None:
        if leader != self.leader or term != self.term:
            raise ProtocolError("heartbeat is not from the current leader and term")
        if tick <= self.last_heartbeat_tick or committed_nonce < self.committed_nonce:
            raise ProtocolError("heartbeat regresses leadership state")
        self.last_heartbeat_tick = tick
        self.committed_nonce = committed_nonce
        self.votes.clear()

    def submit(self, vote: FailureVote) -> LeadershipCertificate | None:
        candidate = self._successor()
        if (
            not self.authority.verify(vote)
            or vote.term != self.term + 1
            or vote.accused != self.leader
            or vote.candidate != candidate
            or vote.observed_tick - self.last_heartbeat_tick < self.timeout_ticks
            or vote.committed_nonce != self.committed_nonce
            or vote.voter == self.leader
        ):
            raise ProtocolError("invalid failover vote")
        existing = self.votes.get(vote.voter)
        if existing:
            if existing == vote:
                return None
            raise ProtocolError("failover voter equivocated")
        if self.votes and vote.observed_tick != next(iter(self.votes.values())).observed_tick:
            raise ProtocolError("failover votes do not describe the same timeout")
        self.votes[vote.voter] = vote
        if len(self.votes) < self.QUORUM:
            return None
        signed_votes = tuple(self.votes[voter] for voter in sorted(self.votes))
        payload = {
            "committed_nonce": self.committed_nonce,
            "leader": candidate,
            "term": self.term + 1,
            "votes": tuple(asdict(vote) for vote in signed_votes),
        }
        certificate = LeadershipCertificate(
            term=payload["term"],
            leader=payload["leader"],
            committed_nonce=payload["committed_nonce"],
            votes=signed_votes,
            digest=hashlib.sha256(canonical_json(payload)).hexdigest(),
        )
        self.term += 1
        self.leader = candidate
        self.last_heartbeat_tick = next(iter(self.votes.values())).observed_tick
        self.votes.clear()
        self.certificates.append(certificate)
        return certificate

    def _successor(self) -> str:
        index = ROLE_ORDER.index(self.leader)
        if index + 1 >= len(ROLE_ORDER):
            raise ProtocolError("all ordered leaders are exhausted")
        return ROLE_ORDER[index + 1]

    def accept_certificate(self, certificate: LeadershipCertificate) -> None:
        """Adopt a peer's next term only after verifying its entire signed transition."""
        if certificate in self.certificates:
            if certificate == self.certificates[-1]:
                return
            raise ProtocolError("stale leadership certificate")
        if certificate.term != self.term + 1 or certificate.leader != self._successor():
            raise ProtocolError("leadership certificate is not the next term")
        if certificate.committed_nonce != self.committed_nonce:
            raise ProtocolError("leadership certificate does not match local committed position")
        if not certificate.votes:
            raise ProtocolError("leadership certificate has no timeout votes")
        candidate = self.snapshot()
        candidate["term"] = certificate.term
        candidate["leader"] = certificate.leader
        candidate["last_heartbeat_tick"] = certificate.votes[0].observed_tick
        candidate["certificates"].append(asdict(certificate))
        verified = self.from_snapshot(self.authority, candidate)
        if verified.last_heartbeat_tick < self.last_heartbeat_tick:
            raise ProtocolError("leadership certificate regresses heartbeat time")
        self.term = verified.term
        self.leader = verified.leader
        self.last_heartbeat_tick = verified.last_heartbeat_tick
        self.votes.clear()
        self.certificates.append(certificate)

    def snapshot(self) -> dict:
        return {
            "schema": "splitchain-leadership/v1",
            "timeout_ticks": self.timeout_ticks,
            "term": self.term,
            "leader": self.leader,
            "last_heartbeat_tick": self.last_heartbeat_tick,
            "committed_nonce": self.committed_nonce,
            "certificates": [asdict(value) for value in self.certificates],
        }

    @classmethod
    def from_snapshot(cls, authority: FailoverAuthority, snapshot: dict) -> LeadershipState:
        if snapshot.get("schema") != "splitchain-leadership/v1":
            raise ProtocolError("unsupported leadership snapshot")
        try:
            state = cls(authority, int(snapshot["timeout_ticks"]))
            state.term = int(snapshot["term"])
            state.leader = str(snapshot["leader"])
            state.last_heartbeat_tick = int(snapshot["last_heartbeat_tick"])
            state.committed_nonce = int(snapshot["committed_nonce"])
            state.certificates = []
            for value in snapshot["certificates"]:
                certificate = LeadershipCertificate(
                    term=int(value["term"]),
                    leader=str(value["leader"]),
                    committed_nonce=int(value["committed_nonce"]),
                    votes=tuple(FailureVote(**vote) for vote in value["votes"]),
                    digest=str(value["digest"]),
                )
                state.certificates.append(certificate)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid leadership snapshot") from exc
        if (
            state.leader not in ROLE_ORDER
            or state.term != len(state.certificates)
            or state.term < 0
            or state.term >= len(ROLE_ORDER)
            or state.last_heartbeat_tick < 0
            or state.committed_nonce < 0
        ):
            raise ProtocolError("leadership snapshot violates term history")
        expected_leader = ROLE_ORDER[0]
        previous_tick = 0
        previous_nonce = 0
        for expected_term, certificate in enumerate(state.certificates, 1):
            expected_leader = ROLE_ORDER[ROLE_ORDER.index(expected_leader) + 1]
            payload = {
                "committed_nonce": certificate.committed_nonce,
                "leader": certificate.leader,
                "term": certificate.term,
                "votes": tuple(asdict(vote) for vote in certificate.votes),
            }
            digest = hashlib.sha256(canonical_json(payload)).hexdigest()
            if (
                certificate.term != expected_term
                or certificate.leader != expected_leader
                or len(certificate.votes) < cls.QUORUM
                or len(set(certificate.voters)) != len(certificate.votes)
                or certificate.committed_nonce < previous_nonce
                or not all(
                    authority.verify(vote)
                    and vote.term == certificate.term
                    and vote.accused == ROLE_ORDER[expected_term - 1]
                    and vote.candidate == certificate.leader
                    and vote.voter != vote.accused
                    and vote.committed_nonce == certificate.committed_nonce
                    and vote.observed_tick == certificate.votes[0].observed_tick
                    and vote.observed_tick - previous_tick >= state.timeout_ticks
                    for vote in certificate.votes
                )
                or digest != certificate.digest
            ):
                raise ProtocolError("invalid leadership certificate history")
            previous_tick = certificate.votes[0].observed_tick
            previous_nonce = certificate.committed_nonce
        if expected_leader != state.leader:
            raise ProtocolError("leadership certificate does not match current leader")
        if state.last_heartbeat_tick < previous_tick or state.committed_nonce < previous_nonce:
            raise ProtocolError("leadership snapshot regresses certified state")
        return state
