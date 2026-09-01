import pytest

from splitchain.model import BranchState, Ledger, ProtocolError, protocol_digest


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


def test_branch_is_bound_to_recognized_canonical_split_point():
    ledger = Ledger({"alice": 100, "bob": 0})
    branch = ledger.offer("alice", "bob", 10)

    assert branch.origin_height == 0
    assert branch.origin_digest == ledger.canonical_history[0]

    ledger.accept(branch.branch_id, "bob")
    ledger.commit(branch.branch_id, "alice", {"payment": 10})
    ledger.advance(3)

    assert ledger.canonical_height == 1
    assert ledger.canonical_history[1] == ledger.canonical_digest
    next_branch = ledger.offer("alice", "bob", 5)
    assert next_branch.origin_height == 1
    assert next_branch.origin_digest == ledger.canonical_digest


def test_protocol_hashes_are_canonical_and_domain_separated():
    left = protocol_digest("splitchain/test-a/v1", {"b": 2, "a": 1})
    reordered = protocol_digest("splitchain/test-a/v1", {"a": 1, "b": 2})
    other_domain = protocol_digest("splitchain/test-b/v1", {"a": 1, "b": 2})

    assert left == reordered
    assert left != other_domain


def test_non_finite_json_commit_is_rejected():
    ledger = Ledger({"alice": 100})
    branch = ledger.offer("alice", "bob", 10)
    ledger.accept(branch.branch_id, "bob")

    with pytest.raises(ProtocolError, match="canonical JSON"):
        ledger.commit(branch.branch_id, "alice", {"invalid": float("nan")})
