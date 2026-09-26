from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.membership import JointStakeMembership, StakeMembership
from splitchain.model import ProtocolError
from splitchain.stake_votes import (
    JointStakeVoteBooks,
    StakeDecision,
    StakeVoteBook,
    sign_vote,
)


def setup_book(stakes):
    keys = {identity: Ed25519PrivateKey.generate() for identity, _ in stakes.allocations}
    book = StakeVoteBook(stakes, "test-network", {
        identity: key.public_key() for identity, key in keys.items()
    })
    return book, keys


def decision(book, value=2, digest="transfer-a", kind="mutation", position=7):
    return StakeDecision("test-network", book.membership.epoch,
                         book.membership.digest(), kind, position, digest, value)


def test_signed_stake_votes_require_distinct_eligible_backed_weight():
    membership = StakeMembership(0, (("a", 4), ("b", 4), ("c", 4), ("candidate", 0)), 12)
    book, keys = setup_book(membership)
    target = decision(book, value=4)
    assert book.submit(sign_vote(keys["a"], "a", target)) is False
    assert book.submit(sign_vote(keys["a"], "a", target)) is False
    assert book.submit(sign_vote(keys["b"], "b", target)) is False
    with pytest.raises(ProtocolError, match="does not cover"):
        book.submit(sign_vote(keys["candidate"], "candidate", target))
    assert book.submit(sign_vote(keys["c"], "c", target)) is True
    assert StakeVoteBook.from_snapshot(
        membership, "test-network", book.public_keys, book.snapshot()
    ).snapshot() == book.snapshot()


def test_forgery_equivocation_wrong_epoch_and_transaction_size_fail_closed():
    membership = StakeMembership(0, (("a", 6), ("b", 6), ("candidate", 0)), 12)
    book, keys = setup_book(membership)
    target = decision(book, value=5)
    with pytest.raises(ProtocolError, match="signature"):
        book.submit(sign_vote(keys["b"], "a", target))
    with pytest.raises(ProtocolError, match="another network or epoch"):
        book.submit(sign_vote(keys["a"], "a", replace(target, epoch=1)))
    with pytest.raises(ProtocolError, match="does not cover"):
        book.submit(sign_vote(keys["a"], "a", decision(book, value=7)))
    assert book.submit(sign_vote(keys["a"], "a", target)) is False
    with pytest.raises(ProtocolError, match="conflicting decisions"):
        book.submit(sign_vote(keys["a"], "a", replace(target, digest="transfer-b")))
    assert book.submit(sign_vote(keys["b"], "b", target)) is True
    tampered = book.snapshot()
    tampered["votes"][0]["decision"]["digest"] = "transfer-b"
    with pytest.raises(ProtocolError, match="snapshot"):
        StakeVoteBook.from_snapshot(membership, "test-network", book.public_keys, tampered)


def test_joint_quorum_needs_independently_signed_decisions_in_both_epochs():
    previous = StakeMembership(0, (("a", 5), ("b", 5), ("c", 5)), 15)
    candidate = StakeMembership(1, (("a", 5), ("b", 5), ("c", 3), ("d", 2)), 15)
    old_book, old_keys = setup_book(previous)
    new_book, new_keys = setup_book(candidate)
    joint = JointStakeVoteBooks(JointStakeMembership(previous, candidate),
                               old_book, new_book)
    for voter in ("a", "b", "c"):
        old_book.submit(sign_vote(old_keys[voter], voter, decision(old_book)))
    assert not joint.approves("mutation", 7, "transfer-a", 2)
    for voter in ("a", "b"):
        new_book.submit(sign_vote(new_keys[voter], voter, decision(new_book)))
    assert not joint.approves("mutation", 7, "transfer-a", 2)
    new_book.submit(sign_vote(new_keys["d"], "d", decision(new_book)))
    assert joint.approves("mutation", 7, "transfer-a", 2)
