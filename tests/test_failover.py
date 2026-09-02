import pytest

from splitchain.failover import FailoverAuthority, LeadershipState
from splitchain.model import ProtocolError

KEYS = {
    "primary": "primary-failover-key-at-least-32-bytes",
    "secondary": "secondary-failover-key-at-least-32-bytes",
    "tertiary": "tertiary-failover-key-at-least-32-bytes",
}


def vote(authority, voter, term=1, accused="primary", candidate="secondary", tick=4, nonce=0):
    return authority.vote(voter, term, accused, candidate, tick, nonce)


def test_two_of_three_timeout_votes_certify_secondary():
    authority = FailoverAuthority(KEYS)
    state = LeadershipState(authority)
    assert state.submit(vote(authority, "secondary")) is None
    certificate = state.submit(vote(authority, "tertiary"))
    assert certificate.leader == "secondary"
    assert certificate.voters == ("secondary", "tertiary")
    assert state.term == 1


def test_heartbeat_prevents_premature_failover_and_clears_votes():
    authority = FailoverAuthority(KEYS)
    state = LeadershipState(authority)
    with pytest.raises(ProtocolError, match="invalid failover vote"):
        state.submit(vote(authority, "secondary", tick=2))
    state.submit(vote(authority, "secondary", tick=4))
    state.heartbeat("primary", 0, tick=3, committed_nonce=1)
    assert state.votes == {}


def test_failover_rejects_position_mismatch_and_equivocation():
    authority = FailoverAuthority(KEYS)
    state = LeadershipState(authority)
    state.heartbeat("primary", 0, tick=1, committed_nonce=4)
    with pytest.raises(ProtocolError, match="invalid failover vote"):
        state.submit(vote(authority, "secondary", tick=4, nonce=3))
    state.submit(vote(authority, "secondary", tick=4, nonce=4))
    with pytest.raises(ProtocolError, match="equivocated"):
        state.submit(vote(authority, "secondary", tick=5, nonce=4))


def test_durable_state_preserves_certificate_and_rejects_tampering():
    authority = FailoverAuthority(KEYS)
    state = LeadershipState(authority)
    state.submit(vote(authority, "secondary"))
    state.submit(vote(authority, "tertiary"))
    recovered = LeadershipState.from_snapshot(authority, state.snapshot())
    assert recovered.snapshot() == state.snapshot()
    corrupted = state.snapshot()
    corrupted["leader"] = "primary"
    with pytest.raises(ProtocolError, match="certificate"):
        LeadershipState.from_snapshot(authority, corrupted)
    corrupted = state.snapshot()
    corrupted["certificates"][0]["votes"][0]["committed_nonce"] = 99
    with pytest.raises(ProtocolError, match="certificate"):
        LeadershipState.from_snapshot(authority, corrupted)


def test_secondary_then_tertiary_succession_and_exhaustion():
    authority = FailoverAuthority(KEYS)
    state = LeadershipState(authority)
    state.submit(vote(authority, "secondary"))
    state.submit(vote(authority, "tertiary"))
    state.submit(vote(
        authority, "primary", term=2, accused="secondary", candidate="tertiary", tick=8
    ))
    state.submit(vote(
        authority, "tertiary", term=2, accused="secondary", candidate="tertiary", tick=8
    ))
    assert state.leader == "tertiary"
    with pytest.raises(ProtocolError, match="exhausted"):
        state.submit(vote(authority, "primary", term=3, accused="tertiary", tick=12))
