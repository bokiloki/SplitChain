import pytest

from splitchain.model import BranchState, Ledger, ProtocolError


def test_happy_path_finalizes_after_three_rounds():
    ledger = Ledger({"alice": 100, "bob": 0})
    branch = ledger.offer("alice", "bob", 20)
    assert ledger.locked["alice"] == 40
    ledger.accept(branch.branch_id, "bob")
    ledger.commit(branch.branch_id, "alice", {"payment": 20})
    ledger.advance(2)
    assert branch.state == BranchState.COMMITTED
    ledger.advance()
    assert branch.state == BranchState.FINAL
    assert ledger.balances == {"alice": 80, "bob": 20}
    assert ledger.locked["alice"] == 0


def test_branch_allows_only_one_commit():
    ledger = Ledger({"alice": 100})
    branch = ledger.offer("alice", "bob", 10)
    ledger.accept(branch.branch_id, "bob")
    ledger.commit(branch.branch_id, "alice", {"payment": 10})
    with pytest.raises(ProtocolError):
        ledger.commit(branch.branch_id, "alice", {"payment": 11})


def test_equal_stake_and_value_are_required_by_construction():
    ledger = Ledger({"alice": 19})
    with pytest.raises(ProtocolError):
        ledger.offer("alice", "bob", 10)


def test_uncommitted_branch_expires_and_unlocks():
    ledger = Ledger({"alice": 100})
    branch = ledger.offer("alice", "bob", 10, ttl=3)
    ledger.advance(3)
    assert branch.state == BranchState.EXPIRED
    assert ledger.available("alice") == 100

