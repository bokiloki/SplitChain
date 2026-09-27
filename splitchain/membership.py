"""Immutable membership and joint-quorum rules for a future validator transition.

This module does not change the current three-node reference cluster.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .model import ProtocolError, protocol_digest

if TYPE_CHECKING:
    from .model import Ledger


@dataclass(frozen=True)
class Membership:
    epoch: int
    validators: tuple[str, ...]

    def __post_init__(self) -> None:
        if (self.epoch < 0 or len(self.validators) < 3
                or len(set(self.validators)) != len(self.validators)
                or any(not isinstance(name, str) or not name for name in self.validators)):
            raise ProtocolError("invalid validator membership")

    @property
    def quorum(self) -> int:
        return (2 * len(self.validators) + 2) // 3

    def digest(self) -> str:
        return protocol_digest("splitchain/membership/v1", {
            "epoch": self.epoch, "validators": self.validators,
        })

    def approves(self, voters: set[str]) -> bool:
        return len(voters.intersection(self.validators)) >= self.quorum


@dataclass(frozen=True)
class JointMembership:
    previous: Membership
    candidate: Membership

    def __post_init__(self) -> None:
        if (self.candidate.epoch != self.previous.epoch + 1
                or not set(self.previous.validators).issubset(self.candidate.validators)):
            raise ProtocolError("invalid validator transition")

    def approves(self, voters: set[str]) -> bool:
        """During transition, neither the old nor the new quorum can decide alone."""
        return self.previous.approves(voters) and self.candidate.approves(voters)


@dataclass(frozen=True)
class StakeMembership:
    """Proposed voting policy; live replication has not adopted stake epochs yet."""

    epoch: int
    allocations: tuple[tuple[str, int], ...]
    backing_limit: int

    @classmethod
    def from_locked_reserve(
        cls, ledger: Ledger, reserve_account: str, epoch: int,
        allocations: tuple[tuple[str, int], ...],
    ) -> StakeMembership:
        """Bound a proposed allocation to an unchanged genesis-locked balance."""
        if (
            ledger.genesis is None or reserve_account not in ledger.genesis.locked_accounts
            or ledger.balances.get(reserve_account) != ledger.genesis.allocations[reserve_account]
        ):
            raise ProtocolError("validator stake requires an intact locked genesis reserve")
        return cls(epoch, allocations, ledger.balances[reserve_account])

    def __post_init__(self) -> None:
        identities = [identity for identity, _ in self.allocations]
        if (
            type(self.epoch) is not int or self.epoch < 0
            or type(self.backing_limit) is not int or self.backing_limit < 1
            or not identities or len(set(identities)) != len(identities)
            or any(not isinstance(identity, str) or not identity for identity in identities)
            or any(type(amount) is not int or amount < 0 for _, amount in self.allocations)
            or sum(amount for _, amount in self.allocations) == 0
            or sum(amount for _, amount in self.allocations) > self.backing_limit
        ):
            raise ProtocolError("invalid validator stake allocation")

    @property
    def total_stake(self) -> int:
        return sum(amount for _, amount in self.allocations)

    @property
    def quorum_stake(self) -> int:
        return (2 * self.total_stake) // 3 + 1

    def digest(self) -> str:
        return protocol_digest("splitchain/stake-membership/v1", {
            "epoch": self.epoch,
            "backing_limit": self.backing_limit,
            "allocations": tuple(sorted(self.allocations)),
        })

    def approves(self, voters: set[str], transaction_value: int) -> bool:
        if type(transaction_value) is not int or transaction_value < 1:
            raise ProtocolError("transaction value must be positive")
        if not isinstance(voters, set):
            raise ProtocolError("voters must be distinct identities")
        return sum(
            amount for identity, amount in self.allocations
            if identity in voters and amount >= transaction_value
        ) >= self.quorum_stake

    def proportional_delegation(self, departing: str, recipients: set[str]) -> StakeMembership:
        """Calculate a proposed epoch; timeouts never activate it by themselves.

        Assign integer remainders by largest fractional share, breaking ties
        by identity so all nodes calculate the same allocation.
        """
        weights = dict(self.allocations)
        if (departing not in weights or weights[departing] == 0
                or not recipients or departing in recipients
                or not recipients.issubset(weights)
                or any(weights[name] == 0 for name in recipients)):
            raise ProtocolError("invalid proportional stake delegation")
        amount = weights[departing]
        online = sum(weights[name] for name in recipients)
        portions = {name: divmod(amount * weights[name], online) for name in recipients}
        leftover = amount - sum(quotient for quotient, _ in portions.values())
        ordered = sorted(recipients, key=lambda name: (-portions[name][1], name))
        assigned = {name: portions[name][0] for name in recipients}
        for name in ordered[:leftover]:
            assigned[name] += 1
        weights[departing] = 0
        for name, share in assigned.items():
            weights[name] += share
        return StakeMembership(self.epoch + 1, tuple(sorted(weights.items())),
                               self.backing_limit)


@dataclass(frozen=True)
class JointStakeMembership:
    previous: StakeMembership
    candidate: StakeMembership

    def __post_init__(self) -> None:
        if self.candidate.epoch != self.previous.epoch + 1:
            raise ProtocolError("invalid validator stake transition")

    def approves(self, voters: set[str], transaction_value: int) -> bool:
        return (self.previous.approves(voters, transaction_value)
                and self.candidate.approves(voters, transaction_value))
