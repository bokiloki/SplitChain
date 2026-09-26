"""Immutable membership and joint-quorum rules for a future validator transition.

This module does not change the current three-node reference cluster.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import ProtocolError, protocol_digest


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
