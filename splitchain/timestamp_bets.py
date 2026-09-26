"""Signed timestamp commitments for an isolated, per-validator consensus sandbox."""

from __future__ import annotations

import base64
import binascii
from dataclasses import asdict, dataclass

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from .model import ProtocolError, canonical_json, protocol_digest
from .stake_votes import StakeCertificate, StakeDecision, StakeVote, StakeVoteBook


def bet_hash(epoch_digest: str, transaction_digest: str, target_timestamp_ms: int,
             secret: str) -> str:
    if (not epoch_digest or not transaction_digest or type(target_timestamp_ms) is not int
            or target_timestamp_ms <= 0 or not isinstance(secret, str) or len(secret) < 32):
        raise ProtocolError("invalid timestamp bet material")
    return protocol_digest("splitchain/timestamp-bet/v1", {
        "epoch_digest": epoch_digest,
        "transaction_digest": transaction_digest,
        "target_timestamp_ms": target_timestamp_ms,
        "secret": secret,
    })


@dataclass(frozen=True)
class BetCommit:
    voter: str
    epoch_digest: str
    position: int
    transaction_digest: str
    value: int
    target_round: int
    target_timestamp_ms: int
    commitment: str
    signature: str
    series_id: str = "single"
    sequence_index: int = 1
    sequence_length: int = 1

    def payload(self) -> bytes:
        return canonical_json({"domain": "splitchain/bet-commit/v1", **{
            key: value for key, value in asdict(self).items() if key != "signature"
        }})


@dataclass(frozen=True)
class BetReveal:
    voter: str
    position: int
    secret: str
    signature: str

    def payload(self, epoch_digest: str) -> bytes:
        return canonical_json({
            "domain": "splitchain/bet-reveal/v1", "epoch_digest": epoch_digest,
            "voter": self.voter, "position": self.position, "secret": self.secret,
        })


def next_secret(secret: str) -> str:
    if not isinstance(secret, str) or len(secret) < 32:
        raise ProtocolError("invalid timestamp bet secret")
    return protocol_digest("splitchain/timestamp-bet-next/v1", secret)


@dataclass(frozen=True)
class BetSuffixReveal:
    voter: str
    epoch_digest: str
    series_id: str
    start_index: int
    end_index: int
    starting_secret: str
    signature: str

    def payload(self) -> bytes:
        return canonical_json({"domain": "splitchain/bet-suffix/v1", **{
            key: value for key, value in asdict(self).items() if key != "signature"
        }})


def sign_suffix(key: Ed25519PrivateKey, voter: str, epoch_digest: str,
                series_id: str, start_index: int, end_index: int,
                starting_secret: str) -> BetSuffixReveal:
    unsigned = BetSuffixReveal(voter, epoch_digest, series_id, start_index,
                               end_index, starting_secret, "")
    return BetSuffixReveal(voter, epoch_digest, series_id, start_index,
                           end_index, starting_secret,
                           base64.b64encode(key.sign(unsigned.payload())).decode())


def sign_commit(key: Ed25519PrivateKey, **fields: object) -> BetCommit:
    commit = BetCommit(**fields, signature="")
    return BetCommit(**fields, signature=base64.b64encode(key.sign(commit.payload())).decode())


def sign_reveal(key: Ed25519PrivateKey, voter: str, position: int, secret: str,
                epoch_digest: str) -> BetReveal:
    reveal = BetReveal(voter, position, secret, "")
    return BetReveal(voter, position, secret, base64.b64encode(
        key.sign(reveal.payload(epoch_digest))
    ).decode())


class TimestampBetBook:
    """All validators can replay the signed events using their pinned epoch keys."""

    def __init__(self, votes: StakeVoteBook) -> None:
        self.votes = votes
        self.commits: dict[tuple[str, int], tuple[BetCommit, int]] = {}
        self.reveals: dict[tuple[str, int], tuple[BetReveal, int]] = {}
        self.suffixes: dict[tuple[str, str], tuple[BetSuffixReveal, int]] = {}
        self.blocks: list[tuple[tuple[tuple[str, int], ...], StakeCertificate, str]] = []
        self.accepted: set[tuple[str, int]] = set()

    def block_challenge(self, slots: tuple[tuple[str, int], ...]) -> StakeDecision:
        if not slots or len(set(slots)) != len(slots):
            raise ProtocolError("block requires distinct bet commitments")
        try:
            bets = [self.commits[slot][0] for slot in slots]
        except KeyError as exc:
            raise ProtocolError("block contains an unknown commitment") from exc
        if any(slot in self.accepted for slot in slots):
            raise ProtocolError("block reuses an accepted commitment")
        parent = self.blocks[-1][2] if self.blocks else self.votes.genesis_digest
        digest = protocol_digest("splitchain/stake-bet-block/v1", {
            "parent": parent, "height": len(self.blocks) + 1,
            "epoch_digest": self.votes.epoch_digest,
            "commits": [asdict(bet) for bet in bets],
        })
        return StakeDecision(self.votes.network_id, self.votes.membership.epoch,
                             self.votes.epoch_digest, "block", len(self.blocks) + 1,
                             digest, max(bet.value for bet in bets))

    def accept_block(self, slots: tuple[tuple[str, int], ...],
                     certificate: StakeCertificate) -> None:
        expected = self.block_challenge(slots)
        if certificate.decision != expected:
            raise ProtocolError("stake certificate is for a different next block")
        certificate.verify(self.votes)
        self.blocks.append((slots, certificate, expected.digest))
        self.accepted.update(slots)

    def _verify(self, voter: str, payload: bytes, signature: str) -> None:
        key = self.votes.public_keys.get(voter)
        if key is None:
            raise ProtocolError("unknown timestamp bet voter")
        try:
            key.verify(base64.b64decode(signature, validate=True), payload)
        except (InvalidSignature, binascii.Error, TypeError, ValueError) as exc:
            raise ProtocolError("invalid timestamp bet signature") from exc

    def commit(self, bet: BetCommit, observed_round: int) -> None:
        allocation = dict(self.votes.membership.allocations).get(bet.voter, 0)
        if (
            type(observed_round) is not int or observed_round < 0
            or type(bet.position) is not int or bet.position < 0
            or type(bet.value) is not int or bet.value < 1 or allocation < bet.value
            or bet.epoch_digest != self.votes.epoch_digest
            or not bet.transaction_digest or not bet.commitment
            or type(bet.target_timestamp_ms) is not int or bet.target_timestamp_ms <= 0
            or type(bet.target_round) is not int
            or not observed_round < bet.target_round <= observed_round + 6
            or not isinstance(bet.series_id, str) or not bet.series_id
            or type(bet.sequence_index) is not int or type(bet.sequence_length) is not int
            or not 1 <= bet.sequence_index <= bet.sequence_length <= 100
        ):
            raise ProtocolError("invalid or underfunded timestamp bet")
        self._verify(bet.voter, bet.payload(), bet.signature)
        slot = (bet.voter, bet.position)
        existing = self.commits.get(slot)
        if existing is not None:
            if existing[0] == bet:
                return
            raise ProtocolError("timestamp bet voter equivocated")
        if any(
            saved.voter == bet.voter and saved.series_id == bet.series_id
            and (saved.sequence_index == bet.sequence_index
                 or saved.sequence_length != bet.sequence_length)
            for saved, _ in self.commits.values()
        ) or (bet.voter, bet.series_id) in self.suffixes:
            raise ProtocolError("timestamp bet sequence conflicts with accepted series")
        self.commits[slot] = (bet, observed_round)

    def reveal_suffix(self, suffix: BetSuffixReveal, observed_round: int) -> None:
        if (suffix.epoch_digest != self.votes.epoch_digest
                or not suffix.series_id or type(suffix.start_index) is not int
                or type(suffix.end_index) is not int
                or not 1 <= suffix.start_index <= suffix.end_index <= 100
                or type(observed_round) is not int):
            raise ProtocolError("invalid timestamp suffix reveal")
        self._verify(suffix.voter, suffix.payload(), suffix.signature)
        series = {
            bet.sequence_index: bet for bet, _ in self.commits.values()
            if bet.voter == suffix.voter and bet.series_id == suffix.series_id
        }
        if (len(series) != suffix.end_index
                or set(series) != set(range(1, suffix.end_index + 1))
                or any(bet.sequence_length != suffix.end_index for bet in series.values())
                or any((bet.voter, bet.position) not in self.accepted
                       for bet in series.values())):
            raise ProtocolError("timestamp suffix requires the entire accepted series")
        selected = series[suffix.start_index]
        if not selected.target_round <= observed_round <= selected.target_round + 3:
            raise ProtocolError("timestamp suffix reveal is outside the selected bet window")
        current = suffix.starting_secret
        for index in range(suffix.start_index, suffix.end_index + 1):
            bet = series[index]
            if bet_hash(bet.epoch_digest, bet.transaction_digest,
                        bet.target_timestamp_ms, current) != bet.commitment:
                raise ProtocolError("timestamp suffix does not match accepted hashes")
            current = next_secret(current)
        slot = (suffix.voter, suffix.series_id)
        existing = self.suffixes.get(slot)
        if existing is not None and existing[0] != suffix:
            raise ProtocolError("timestamp suffix voter equivocated")
        self.suffixes[slot] = (suffix, observed_round)

    def reveal(self, item: BetReveal, observed_round: int) -> None:
        slot = (item.voter, item.position)
        stored = self.commits.get(slot)
        if not stored:
            raise ProtocolError("timestamp bet has no commitment")
        bet = stored[0]
        if slot not in self.accepted:
            raise ProtocolError("timestamp bet has not been accepted by a stake quorum")
        if (type(observed_round) is not int
                or not bet.target_round <= observed_round <= bet.target_round + 3
                or bet_hash(bet.epoch_digest, bet.transaction_digest,
                            bet.target_timestamp_ms, item.secret) != bet.commitment):
            raise ProtocolError("invalid or expired timestamp reveal")
        self._verify(item.voter, item.payload(bet.epoch_digest), item.signature)
        existing = self.reveals.get(slot)
        if existing is not None:
            if existing[0] == item:
                return
            raise ProtocolError("timestamp reveal voter equivocated")
        self.reveals[slot] = (item, observed_round)

    def snapshot(self) -> dict:
        return {
            "schema": "splitchain/timestamp-bets/v1", "epoch_digest": self.votes.epoch_digest,
            "commits": [{"bet": asdict(bet), "round": round_number}
                        for bet, round_number in self.commits.values()],
            "reveals": [{"reveal": asdict(reveal), "round": round_number}
                        for reveal, round_number in self.reveals.values()],
            "suffixes": [{"suffix": asdict(suffix), "round": round_number}
                         for suffix, round_number in self.suffixes.values()],
            "blocks": [{"slots": list(slots), "certificate": asdict(cert), "digest": digest}
                       for slots, cert, digest in self.blocks],
        }

    @classmethod
    def from_snapshot(cls, votes: StakeVoteBook, snapshot: dict) -> TimestampBetBook:
        if (not isinstance(snapshot, dict) or snapshot.get("schema") != "splitchain/timestamp-bets/v1"
                or snapshot.get("epoch_digest") != votes.epoch_digest
                or not isinstance(snapshot.get("commits"), list)
                or not isinstance(snapshot.get("reveals"), list)
                or not isinstance(snapshot.get("suffixes"), list)
                or not isinstance(snapshot.get("blocks"), list)):
            raise ProtocolError("invalid timestamp bet snapshot")
        book = cls(votes)
        try:
            for event in snapshot["commits"]:
                bet = BetCommit(**event["bet"])
                if (bet.voter, bet.position) in book.commits:
                    raise ProtocolError("duplicate timestamp bet snapshot entry")
                book.commit(bet, event["round"])
            for entry in snapshot["blocks"]:
                raw_cert = entry["certificate"]
                decision = StakeDecision(**raw_cert["decision"])
                certificate = StakeCertificate(decision, tuple(
                    StakeVote(vote["voter"], StakeDecision(**vote["decision"]),
                              vote["signature"])
                    for vote in raw_cert["votes"]
                ))
                slots = tuple(tuple(slot) for slot in entry["slots"])
                book.accept_block(slots, certificate)
                if book.blocks[-1][2] != entry["digest"]:
                    raise ProtocolError("stake block digest was tampered with")
            for event in snapshot["reveals"]:
                reveal = BetReveal(**event["reveal"])
                if (reveal.voter, reveal.position) in book.reveals:
                    raise ProtocolError("duplicate timestamp bet snapshot entry")
                book.reveal(reveal, event["round"])
            for event in snapshot["suffixes"]:
                suffix = BetSuffixReveal(**event["suffix"])
                if (suffix.voter, suffix.series_id) in book.suffixes:
                    raise ProtocolError("duplicate timestamp suffix snapshot entry")
                book.reveal_suffix(suffix, event["round"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid timestamp bet snapshot") from exc
        return book
