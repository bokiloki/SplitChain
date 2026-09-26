"""Signed stake quorum research core; not connected to the running node protocol."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .membership import JointStakeMembership, StakeMembership
from .model import ProtocolError, canonical_json


@dataclass(frozen=True)
class StakeDecision:
    network_id: str
    epoch: int
    membership_digest: str
    kind: str
    position: int
    digest: str
    transaction_value: int

    def __post_init__(self) -> None:
        if (
            not self.network_id or not self.membership_digest or not self.digest
            or self.kind not in {"mutation", "leadership", "round"}
            or type(self.epoch) is not int or self.epoch < 0
            or type(self.position) is not int or self.position < 0
            or type(self.transaction_value) is not int or self.transaction_value < 1
        ):
            raise ProtocolError("invalid stake decision")


@dataclass(frozen=True)
class StakeVote:
    voter: str
    decision: StakeDecision
    signature: str

    def payload(self) -> bytes:
        return canonical_json({
            "domain": "splitchain/stake-vote/v1",
            "voter": self.voter,
            "decision": vars(self.decision),
        })


def sign_vote(key: Ed25519PrivateKey, voter: str, decision: StakeDecision) -> StakeVote:
    unsigned = StakeVote(voter, decision, "")
    return StakeVote(voter, decision, base64.b64encode(key.sign(unsigned.payload())).decode())


class StakeVoteBook:
    """Verify independent identities and prevent duplicate and conflicting decisions."""

    def __init__(
        self, membership: StakeMembership, network_id: str,
        public_keys: dict[str, Ed25519PublicKey],
    ) -> None:
        if set(public_keys) != {identity for identity, _ in membership.allocations}:
            raise ProtocolError("stake key registry does not match membership")
        self.membership = membership
        self.network_id = network_id
        self.public_keys = public_keys.copy()
        self._decisions: dict[tuple[str, str, int], StakeDecision] = {}
        self._votes: dict[StakeDecision, dict[str, StakeVote]] = {}

    def submit(self, vote: StakeVote) -> bool:
        decision = vote.decision
        if (
            decision.network_id != self.network_id
            or decision.epoch != self.membership.epoch
            or decision.membership_digest != self.membership.digest()
        ):
            raise ProtocolError("stake vote is for another network or epoch")
        allocation = dict(self.membership.allocations).get(vote.voter, 0)
        if allocation < decision.transaction_value or allocation == 0:
            raise ProtocolError("validator stake does not cover the decision")
        key = self.public_keys[vote.voter]
        try:
            key.verify(base64.b64decode(vote.signature, validate=True), vote.payload())
        except (InvalidSignature, ValueError, TypeError, binascii.Error) as exc:
            raise ProtocolError("invalid stake vote signature") from exc
        slot = (vote.voter, decision.kind, decision.position)
        existing = self._decisions.get(slot)
        if existing is not None and existing != decision:
            raise ProtocolError("validator voted for conflicting decisions")
        self._decisions[slot] = decision
        votes = self._votes.setdefault(decision, {})
        if vote.voter in votes and votes[vote.voter] != vote:
            raise ProtocolError("validator submitted a conflicting signature")
        votes[vote.voter] = vote
        return self.membership.approves(set(votes), decision.transaction_value)

    def snapshot(self) -> dict:
        return {
            "schema": "splitchain/stake-votes/v1",
            "network_id": self.network_id,
            "membership_digest": self.membership.digest(),
            "votes": [asdict(vote) for votes in self._votes.values() for vote in votes.values()],
        }

    @classmethod
    def from_snapshot(
        cls, membership: StakeMembership, network_id: str,
        public_keys: dict[str, Ed25519PublicKey], snapshot: dict,
    ) -> StakeVoteBook:
        if (
            not isinstance(snapshot, dict) or snapshot.get("schema") != "splitchain/stake-votes/v1"
            or snapshot.get("network_id") != network_id
            or snapshot.get("membership_digest") != membership.digest()
            or not isinstance(snapshot.get("votes"), list)
        ):
            raise ProtocolError("stake vote snapshot does not match this epoch")
        book = cls(membership, network_id, public_keys)
        try:
            for raw in snapshot["votes"]:
                decision = StakeDecision(**raw["decision"])
                vote = StakeVote(raw["voter"], decision, raw["signature"])
                if vote.voter in book._votes.get(decision, {}):
                    raise ProtocolError("duplicate vote in snapshot")
                book.submit(vote)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid stake vote snapshot") from exc
        return book


class JointStakeVoteBooks:
    """A transition commits only after separate signed votes for both epochs."""

    def __init__(self, transition: JointStakeMembership, previous: StakeVoteBook,
                 candidate: StakeVoteBook) -> None:
        if (previous.membership != transition.previous
                or candidate.membership != transition.candidate
                or previous.network_id != candidate.network_id):
            raise ProtocolError("joint vote books do not match the transition")
        self.previous = previous
        self.candidate = candidate

    def approves(self, kind: str, position: int, digest: str, value: int) -> bool:
        def approved(book: StakeVoteBook) -> bool:
            decision = StakeDecision(
                book.network_id, book.membership.epoch, book.membership.digest(),
                kind, position, digest, value,
            )
            return book.membership.approves(
                set(book._votes.get(decision, {})), value,
            )

        return approved(self.previous) and approved(self.candidate)
