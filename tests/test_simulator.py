import pytest

from splitchain.model import Ledger
from splitchain.simulator import assert_invariants, run


def test_simulation_is_deterministic():
    first = run(seed=42, steps=100)
    second = run(seed=42, steps=100)
    assert first == second
    assert first.accepted_actions + first.rejected_actions == 100


def test_simulator_rejects_unrecognized_branch_origin():
    ledger = Ledger({"alice": 100})
    branch = ledger.offer("alice", "bob", 10)
    branch.origin_digest = "00" * 32

    with pytest.raises(AssertionError, match="recognized canonical split point"):
        assert_invariants(ledger, 100)
