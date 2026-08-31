"""Seeded adversarial simulator for invariant exploration."""

from __future__ import annotations

import random
from dataclasses import dataclass

from .model import BranchState, Ledger, ProtocolError


@dataclass
class SimulationResult:
    seed: int
    steps: int
    accepted_actions: int
    rejected_actions: int
    final_snapshot: dict


def assert_invariants(ledger: Ledger, initial_supply: int) -> None:
    if sum(ledger.balances.values()) != initial_supply:
        raise AssertionError("supply changed")
    if any(value < 0 for value in ledger.balances.values()):
        raise AssertionError("negative balance")
    if any(value < 0 for value in ledger.locked.values()):
        raise AssertionError("negative lock")
    for account in ledger.balances:
        if ledger.available(account) < 0:
            raise AssertionError("overspent account")
    for branch in ledger.branches.values():
        if branch.stake != branch.value:
            raise AssertionError("branch stake differs from value")


def run(seed: int = 1, steps: int = 200, accounts: int = 8) -> SimulationResult:
    rng = random.Random(seed)
    names = [f"node-{i}" for i in range(accounts)]
    ledger = Ledger({name: 1_000 for name in names})
    initial_supply = sum(ledger.balances.values())
    accepted = rejected = 0

    for _ in range(steps):
        action = rng.choice(("offer", "accept", "commit", "cancel", "advance"))
        try:
            active = [b for b in ledger.branches.values() if b.state not in (
                BranchState.FINAL, BranchState.CANCELLED, BranchState.EXPIRED
            )]
            if action == "offer":
                sender, receiver = rng.sample(names, 2)
                ledger.offer(sender, receiver, rng.randint(1, 40), rng.randint(3, 10))
            elif action == "advance":
                ledger.advance(rng.randint(1, 2))
            elif active:
                branch = rng.choice(active)
                if action == "accept":
                    ledger.accept(branch.branch_id, branch.receiver)
                elif action == "commit":
                    ledger.commit(branch.branch_id, branch.sender, {"nonce": rng.randrange(10_000)})
                else:
                    ledger.cancel(branch.branch_id, rng.choice((branch.sender, branch.receiver)))
            else:
                ledger.advance()
            accepted += 1
        except ProtocolError:
            rejected += 1
        assert_invariants(ledger, initial_supply)

    return SimulationResult(seed, steps, accepted, rejected, ledger.snapshot())

