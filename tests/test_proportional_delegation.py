import pytest

from splitchain.membership import StakeMembership
from splitchain.model import ProtocolError


def test_delegation_preserves_total_and_allocates_in_proportion_to_existing_stake():
    membership = StakeMembership(4, (("node1", 6), ("node2", 2),
                                     ("node3", 2), ("offline", 10)), 20)
    proposed = membership.proportional_delegation(
        "offline", {"node1", "node2", "node3"},
    )
    assert dict(proposed.allocations) == {
        "node1": 12, "node2": 4, "node3": 4, "offline": 0,
    }
    assert proposed.total_stake == membership.total_stake == 20
    assert proposed.epoch == 5
    assert dict(membership.allocations)["offline"] == 10


def test_delegation_rounding_is_deterministic_and_rejects_zero_stake_recipients():
    membership = StakeMembership(0, (("a", 3), ("b", 2), ("c", 2),
                                     ("observer", 0), ("offline", 4)), 11)
    result = membership.proportional_delegation("offline", {"a", "b", "c"})
    assert dict(result.allocations) == {"a": 5, "b": 3, "c": 3,
                                        "observer": 0, "offline": 0}
    with pytest.raises(ProtocolError, match="invalid proportional"):
        membership.proportional_delegation("offline", {"a", "observer"})
