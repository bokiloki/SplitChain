from itertools import combinations

import pytest

from splitchain.membership import (
    JointMembership,
    JointStakeMembership,
    Membership,
    StakeMembership,
)
from splitchain.model import ProtocolError


def test_six_member_joint_quorum_requires_both_memberships():
    old = Membership(0, ("primary", "secondary", "tertiary"))
    new = Membership(1, old.validators + (
        "colleague-primary", "colleague-secondary", "colleague-tertiary"))
    joint = JointMembership(old, new)
    assert old.quorum == 2
    assert new.quorum == 4
    assert old.digest() != new.digest()
    assert old.approves({"primary", "secondary"})
    assert new.approves({"tertiary", *new.validators[3:]})
    assert not joint.approves({"primary", "secondary"})
    assert not joint.approves({"tertiary", *new.validators[3:]})
    assert joint.approves({"primary", "secondary", "colleague-primary", "colleague-secondary"})
    assert not new.approves(set(new.validators[3:]))
    # Every six-member quorum intersects every other quorum in >=2 voters.
    for a, b in combinations(list(combinations(new.validators, 4)), 2):
        assert len(set(a).intersection(b)) >= 2


def test_transition_rejects_skipped_epoch_and_removed_validator():
    old = Membership(0, ("a", "b", "c"))
    with pytest.raises(ProtocolError):
        JointMembership(old, Membership(2, ("a", "b", "c", "d")))
    with pytest.raises(ProtocolError):
        JointMembership(old, Membership(1, ("a", "b", "d")))


def test_stake_quorum_ignores_zero_stake_candidates_and_caps_each_approval():
    stakes = StakeMembership(0, (
        ("primary", 4), ("secondary", 4), ("tertiary", 4),
        ("colleague-1", 0), ("colleague-2", 0), ("colleague-3", 0),
    ), backing_limit=12)
    assert stakes.quorum_stake == 9
    assert not stakes.approves({"primary", "secondary", "colleague-1"}, 4)
    assert stakes.approves({"primary", "secondary", "tertiary"}, 4)
    assert not stakes.approves({identity for identity, _ in stakes.allocations}, 5)
    assert not stakes.approves({"colleague-1", "colleague-2", "colleague-3"}, 1)


def test_stake_transition_requires_both_epochs_and_distinct_funded_identities():
    old = StakeMembership(0, (("a", 4), ("b", 4), ("c", 4)), 12)
    new = StakeMembership(1, (("a", 4), ("b", 4), ("c", 2), ("d", 2)), 12)
    joint = JointStakeMembership(old, new)
    assert old.approves({"a", "b", "c"}, 3)
    assert not joint.approves({"a", "b", "c"}, 3)
    assert joint.approves({"a", "b", "c", "d"}, 2)
    assert old.digest() != new.digest()
    with pytest.raises(ProtocolError):
        JointStakeMembership(old, StakeMembership(2, new.allocations, 12))
    with pytest.raises(ProtocolError):
        StakeMembership(0, (("a", 8), ("a", 4)), 12)
    with pytest.raises(ProtocolError):
        StakeMembership(0, (("a", 8), ("b", 8)), 12)


def test_stake_quorums_intersect_at_more_than_one_third_of_backing():
    stakes = StakeMembership(0, tuple((name, 1) for name in "abcdef"), 6)
    approved = [set(voters) for count in range(7)
                for voters in combinations("abcdef", count)
                if stakes.approves(set(voters), 1)]
    assert all(len(a & b) >= 2 for a, b in combinations(approved, 2))
