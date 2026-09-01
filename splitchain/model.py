"""Deterministic in-memory protocol model.

This is an executable specification, not production consensus code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class ProtocolError(ValueError):
    """Raised when a transition violates a protocol invariant."""


class BranchState(str, Enum):
    OFFERED = "offered"
    ACCEPTED = "accepted"
    COMMITTED = "committed"
    FINAL = "final"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


def canonical_json(value: Any) -> bytes:
    """Encode protocol data without whitespace or key-order ambiguity."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("payload is not canonical JSON") from exc


def protocol_digest(domain: str, value: Any) -> str:
    """Hash canonical data with an explicit protocol domain separator."""

    if not domain or "\x00" in domain:
        raise ProtocolError("invalid hash domain")
    return hashlib.sha256(domain.encode("ascii") + b"\x00" + canonical_json(value)).hexdigest()


@dataclass
class Branch:
    branch_id: str
    sender: str
    receiver: str
    value: int
    stake: int
    created_round: int
    expires_round: int
    origin_height: int
    origin_digest: str
    state: BranchState = BranchState.OFFERED
    accepted_round: int | None = None
    committed_round: int | None = None
    tx_digest: str | None = None

    def public(self) -> dict:
        result = asdict(self)
        result["state"] = self.state.value
        return result


class Ledger:
    """Canonical ledger plus temporary two-party execution branches."""

    FINALITY_ROUNDS = 3

    def __init__(self, balances: dict[str, int] | None = None) -> None:
        self.round = 0
        self.balances = dict(balances or {})
        self.locked: dict[str, int] = {}
        self.branches: dict[str, Branch] = {}
        self.finalized: list[dict] = []
        self.canonical_height = 0
        genesis = protocol_digest(
            "splitchain/canonical-genesis/v1",
            {"balances": dict(sorted(self.balances.items()))},
        )
        self.canonical_digest = genesis
        self.canonical_history: dict[int, str] = {0: genesis}

    def available(self, account: str) -> int:
        return self.balances.get(account, 0) - self.locked.get(account, 0)

    def offer(self, sender: str, receiver: str, value: int, ttl: int = 6) -> Branch:
        if value <= 0 or ttl < self.FINALITY_ROUNDS:
            raise ProtocolError("value must be positive and ttl must cover finality")
        if sender == receiver:
            raise ProtocolError("sender and receiver must differ")
        if self.available(sender) < value * 2:
            raise ProtocolError("sender needs value plus equal branch stake")
        nonce = len(self.branches)
        branch_id = protocol_digest(
            "splitchain/branch-id/v1",
            {
                "nonce": nonce,
                "origin_digest": self.canonical_digest,
                "origin_height": self.canonical_height,
                "receiver": receiver,
                "round": self.round,
                "sender": sender,
                "value": value,
            },
        )[:16]
        branch = Branch(
            branch_id,
            sender,
            receiver,
            value,
            value,
            self.round,
            self.round + ttl,
            self.canonical_height,
            self.canonical_digest,
        )
        self.branches[branch_id] = branch
        self.locked[sender] = self.locked.get(sender, 0) + value * 2
        return branch

    def accept(self, branch_id: str, receiver: str) -> Branch:
        branch = self._branch(branch_id, BranchState.OFFERED)
        self._ensure_live(branch)
        if receiver != branch.receiver:
            raise ProtocolError("only the named receiver can accept")
        branch.state = BranchState.ACCEPTED
        branch.accepted_round = self.round
        return branch

    def commit(self, branch_id: str, sender: str, payload: dict) -> Branch:
        branch = self._branch(branch_id, BranchState.ACCEPTED)
        self._ensure_live(branch)
        if sender != branch.sender:
            raise ProtocolError("only the sender can commit")
        branch.tx_digest = protocol_digest("splitchain/tx-commit/v1", payload)
        branch.committed_round = self.round
        branch.state = BranchState.COMMITTED
        return branch

    def cancel(self, branch_id: str, actor: str) -> Branch:
        branch = self._branch(branch_id, BranchState.OFFERED, BranchState.ACCEPTED)
        if actor not in (branch.sender, branch.receiver):
            raise ProtocolError("only a participant can cancel")
        branch.state = BranchState.CANCELLED
        self._unlock(branch)
        return branch

    def advance(self, rounds: int = 1) -> list[Branch]:
        if rounds < 1:
            raise ProtocolError("rounds must be positive")
        changed: list[Branch] = []
        for _ in range(rounds):
            self.round += 1
            for branch in self.branches.values():
                if branch.state in (BranchState.OFFERED, BranchState.ACCEPTED):
                    if self.round >= branch.expires_round:
                        branch.state = BranchState.EXPIRED
                        self._unlock(branch)
                        changed.append(branch)
                elif branch.state == BranchState.COMMITTED:
                    assert branch.committed_round is not None
                    if self.round - branch.committed_round >= self.FINALITY_ROUNDS:
                        self._finalize(branch)
                        changed.append(branch)
        return changed

    def snapshot(self) -> dict:
        return {
            "schema": "splitchain-ledger/v1",
            "round": self.round,
            "canonical_head": {
                "height": self.canonical_height,
                "digest": self.canonical_digest,
            },
            "balances": dict(sorted(self.balances.items())),
            "locked": dict(sorted(self.locked.items())),
            "branches": [b.public() for b in self.branches.values()],
            "finalized": list(self.finalized),
            "canonical_history": {
                str(height): digest for height, digest in sorted(self.canonical_history.items())
            },
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> Ledger:
        """Restore a ledger while rechecking invariants at the trust boundary."""

        if snapshot.get("schema") != "splitchain-ledger/v1":
            raise ProtocolError("unsupported ledger snapshot schema")
        ledger = cls(snapshot.get("balances"))
        try:
            ledger.round = int(snapshot["round"])
            head = snapshot["canonical_head"]
            ledger.canonical_height = int(head["height"])
            ledger.canonical_digest = str(head["digest"])
            ledger.locked = {str(k): int(v) for k, v in snapshot["locked"].items()}
            ledger.finalized = list(snapshot["finalized"])
            ledger.canonical_history = {
                int(height): str(digest)
                for height, digest in snapshot["canonical_history"].items()
            }
            ledger.branches = {}
            for raw in snapshot["branches"]:
                data = dict(raw)
                data["state"] = BranchState(data["state"])
                branch = Branch(**data)
                ledger.branches[branch.branch_id] = branch
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid ledger snapshot") from exc
        ledger._validate_restored_state()
        return ledger

    def _validate_restored_state(self) -> None:
        if self.round < 0 or self.canonical_height < 0:
            raise ProtocolError("negative snapshot counter")
        if self.canonical_history.get(self.canonical_height) != self.canonical_digest:
            raise ProtocolError("canonical head is not present in history")
        if any(value < 0 for value in self.balances.values()):
            raise ProtocolError("snapshot contains a negative balance")
        expected_locked: dict[str, int] = {}
        active = {BranchState.OFFERED, BranchState.ACCEPTED, BranchState.COMMITTED}
        for branch in self.branches.values():
            if branch.value <= 0 or branch.stake != branch.value:
                raise ProtocolError("snapshot violates equal branch stake")
            if self.canonical_history.get(branch.origin_height) != branch.origin_digest:
                raise ProtocolError("branch origin is not canonical")
            if branch.state in active:
                expected_locked[branch.sender] = (
                    expected_locked.get(branch.sender, 0) + branch.value + branch.stake
                )
        actual_locked = {key: value for key, value in self.locked.items() if value}
        if expected_locked != actual_locked:
            raise ProtocolError("snapshot locked funds do not match active branches")
        for account in set(self.balances) | set(self.locked):
            if self.available(account) < 0:
                raise ProtocolError("snapshot over-locks an account")

    def _branch(self, branch_id: str, *states: BranchState) -> Branch:
        try:
            branch = self.branches[branch_id]
        except KeyError as exc:
            raise ProtocolError("unknown branch") from exc
        if branch.state not in states:
            raise ProtocolError(f"invalid transition from {branch.state.value}")
        return branch

    def _ensure_live(self, branch: Branch) -> None:
        if self.round >= branch.expires_round:
            raise ProtocolError("branch expired")

    def _unlock(self, branch: Branch) -> None:
        self.locked[branch.sender] -= branch.value + branch.stake

    def _finalize(self, branch: Branch) -> None:
        self.balances[branch.sender] -= branch.value
        self.balances[branch.receiver] = self.balances.get(branch.receiver, 0) + branch.value
        self._unlock(branch)
        branch.state = BranchState.FINAL
        self.finalized.append({"branch_id": branch.branch_id, "tx_digest": branch.tx_digest})
        self.canonical_height += 1
        self.canonical_digest = protocol_digest(
            "splitchain/canonical-block/v1",
            {
                "branch_id": branch.branch_id,
                "height": self.canonical_height,
                "parent": self.canonical_digest,
                "tx_digest": branch.tx_digest,
            },
        )
        self.canonical_history[self.canonical_height] = self.canonical_digest
