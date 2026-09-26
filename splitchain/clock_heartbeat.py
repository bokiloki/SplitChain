"""Signed round heartbeats for lag and clock-skew observation, not finality."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .model import ProtocolError, canonical_json
from .stake_votes import StakeVoteBook


@dataclass(frozen=True)
class RoundHeartbeat:
    voter: str
    epoch_digest: str
    sequence: int
    round: int
    ledger_digest: str
    sent_at_ms: int
    signature: str

    def payload(self) -> bytes:
        return canonical_json({"domain": "splitchain/round-heartbeat/v1", **{
            key: value for key, value in asdict(self).items() if key != "signature"
        }})


def sign_heartbeat(key: Ed25519PrivateKey, *, voter: str, epoch_digest: str,
                   sequence: int, round_number: int, ledger_digest: str,
                   sent_at_ms: int) -> RoundHeartbeat:
    heartbeat = RoundHeartbeat(voter, epoch_digest, sequence, round_number,
                               ledger_digest, sent_at_ms, "")
    return RoundHeartbeat(voter, epoch_digest, sequence, round_number,
                          ledger_digest, sent_at_ms,
                          base64.b64encode(key.sign(heartbeat.payload())).decode())


class ClockTracker:
    """Never adjust a local clock or commit a round from heartbeat reports alone."""

    def __init__(self, book: StakeVoteBook, max_skew_ms: int = 5000) -> None:
        if type(max_skew_ms) is not int or max_skew_ms < 0:
            raise ProtocolError("invalid heartbeat skew bound")
        self.book = book
        self.max_skew_ms = max_skew_ms
        self.latest: dict[str, tuple[RoundHeartbeat, int, bool]] = {}

    def observe(self, event: RoundHeartbeat, received_at_ms: int) -> bool:
        key = self.book.public_keys.get(event.voter)
        if (
            key is None or event.epoch_digest != self.book.epoch_digest
            or type(event.sequence) is not int or event.sequence < 1
            or type(event.round) is not int or event.round < 0
            or type(event.sent_at_ms) is not int or event.sent_at_ms < 0
            or type(received_at_ms) is not int or received_at_ms < 0
            or not event.ledger_digest
        ):
            raise ProtocolError("invalid round heartbeat")
        try:
            key.verify(base64.b64decode(event.signature, validate=True), event.payload())
        except (InvalidSignature, binascii.Error, TypeError, ValueError) as exc:
            raise ProtocolError("invalid round heartbeat signature") from exc
        previous = self.latest.get(event.voter)
        if previous and event.sequence <= previous[0].sequence:
            if event == previous[0]:
                return previous[2]
            raise ProtocolError("replayed or regressed round heartbeat")
        skewed = abs(event.sent_at_ms - received_at_ms) > self.max_skew_ms
        self.latest[event.voter] = (event, received_at_ms, skewed)
        return skewed

    def observed_stake(self, round_number: int, ledger_digest: str) -> int:
        allocations = dict(self.book.membership.allocations)
        return sum(
            allocations[voter] for voter, (event, _, skewed) in self.latest.items()
            if not skewed and event.round == round_number and event.ledger_digest == ledger_digest
        )

    def snapshot(self) -> dict:
        return {"schema": "splitchain/clock-heartbeats/v1",
                "epoch_digest": self.book.epoch_digest, "max_skew_ms": self.max_skew_ms,
                "latest": [{"heartbeat": asdict(event), "received_at_ms": received}
                           for event, received, _ in self.latest.values()]}

    @classmethod
    def from_snapshot(cls, book: StakeVoteBook, snapshot: dict) -> ClockTracker:
        if (not isinstance(snapshot, dict)
                or snapshot.get("schema") != "splitchain/clock-heartbeats/v1"
                or snapshot.get("epoch_digest") != book.epoch_digest
                or not isinstance(snapshot.get("latest"), list)):
            raise ProtocolError("invalid clock heartbeat snapshot")
        tracker = cls(book, snapshot["max_skew_ms"])
        try:
            for item in snapshot["latest"]:
                event = RoundHeartbeat(**item["heartbeat"])
                if event.voter in tracker.latest:
                    raise ProtocolError("duplicate heartbeat identity in snapshot")
                tracker.observe(event, item["received_at_ms"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid clock heartbeat snapshot") from exc
        return tracker
