"""Term-based, quorum-certified leadership failover safety core."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass

from .model import ProtocolError, canonical_json

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
    def __init__(self, node_keys: dict[str, str]) -> None:
        if set(node_keys) != set(ROLE_ORDER) or any(len(key) < 32 for key in node_keys.values()):
            raise ProtocolError("failover requires a strong key for every ordered role")
        self._keys = {node: key.encode() for node, key in node_keys.items()}

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
        try:
            signature = hmac.new(
                self._keys[voter], canonical_json(unsigned), hashlib.sha256
            ).hexdigest()
        except KeyError as exc:
            raise ProtocolError("unknown failover voter") from exc
        return FailureVote(signature=signature, **unsigned)

    def verify(self, vote: FailureVote) -> bool:
        key = self._keys.get(vote.voter)
        if not key:
            return False
        expected = hmac.new(key, canonical_json(vote.unsigned()), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, vote.signature)


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
        if state.leader not in ROLE_ORDER or state.term != len(state.certificates):
            raise ProtocolError("leadership snapshot violates term history")
        expected_leader = ROLE_ORDER[0]
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
                or not all(authority.verify(vote) for vote in certificate.votes)
                or digest != certificate.digest
            ):
                raise ProtocolError("invalid leadership certificate history")
        if expected_leader != state.leader:
            raise ProtocolError("leadership certificate does not match current leader")
        return state
