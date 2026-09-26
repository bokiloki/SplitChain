from itertools import combinations

import pytest

from splitchain.membership import JointMembership, Membership
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
