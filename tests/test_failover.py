import hashlib
from dataclasses import asdict, replace

import pytest

from splitchain.failover import FailoverAuthority, LeadershipState
from splitchain.model import ProtocolError, canonical_json

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


def test_recovery_rejects_signed_votes_for_wrong_transition():
    authority = FailoverAuthority(KEYS)
    state = LeadershipState(authority)
    state.submit(vote(authority, "secondary"))
    state.submit(vote(authority, "tertiary"))
    snapshot = state.snapshot()
    certificate = snapshot["certificates"][0]
    certificate["votes"] = [
        asdict(vote(authority, voter, accused="tertiary"))
        for voter in ("secondary", "tertiary")
    ]
    certificate["digest"] = hashlib.sha256(canonical_json({
        "committed_nonce": certificate["committed_nonce"],
        "leader": certificate["leader"],
        "term": certificate["term"],
        "votes": tuple(certificate["votes"]),
    })).hexdigest()
    with pytest.raises(ProtocolError, match="certificate"):
        LeadershipState.from_snapshot(authority, snapshot)


@pytest.mark.parametrize("field,value", [
    ("last_heartbeat_tick", 3),
    ("committed_nonce", -1),
    ("term", 3),
])
def test_recovery_rejects_regressed_or_impossible_state(field, value):
    authority = FailoverAuthority(KEYS)
    state = LeadershipState(authority)
    state.submit(vote(authority, "secondary"))
    state.submit(vote(authority, "tertiary"))
    snapshot = state.snapshot()
    snapshot[field] = value
    with pytest.raises(ProtocolError):
        LeadershipState.from_snapshot(authority, snapshot)


def test_follower_accepts_certified_transition_once():
    authority = FailoverAuthority(KEYS)
    leader = LeadershipState(authority)
    follower = LeadershipState(authority)
    leader.submit(vote(authority, "secondary"))
    certificate = leader.submit(vote(authority, "tertiary"))
    follower.accept_certificate(certificate)
    follower.accept_certificate(certificate)
    assert follower.snapshot() == leader.snapshot()


def test_stale_primary_adopts_majority_certificate_after_local_heartbeat():
    authority = FailoverAuthority(KEYS)
    majority = LeadershipState(authority)
    majority.submit(vote(authority, "secondary", tick=4))
    certificate = majority.submit(vote(authority, "tertiary", tick=4))

    stale_primary = LeadershipState(authority)
    stale_primary.heartbeat("primary", 0, tick=7, committed_nonce=0)
    stale_primary.accept_certificate(certificate)

    assert stale_primary.leader == "secondary"
    assert stale_primary.term == 1
    assert LeadershipState.from_snapshot(authority, stale_primary.snapshot()).leader == "secondary"


def test_follower_rejects_certificate_before_catching_up():
    authority = FailoverAuthority(KEYS)
    leader = LeadershipState(authority)
    follower = LeadershipState(authority)
    leader.heartbeat("primary", 0, 1, 5)
    leader.submit(vote(authority, "secondary", tick=4, nonce=5))
    certificate = leader.submit(vote(authority, "tertiary", tick=4, nonce=5))
    original = follower.snapshot()
    with pytest.raises(ProtocolError, match="committed position"):
        follower.accept_certificate(certificate)
    assert follower.snapshot() == original
    follower.heartbeat("primary", 0, 1, 5)
    follower.accept_certificate(certificate)
    assert follower.snapshot() == leader.snapshot()


def test_follower_rejects_conflicting_or_stale_certificate():
    authority = FailoverAuthority(KEYS)
    leader = LeadershipState(authority)
    follower = LeadershipState(authority)
    leader.submit(vote(authority, "secondary"))
    certificate = leader.submit(vote(authority, "tertiary"))
    with pytest.raises(ProtocolError, match="certificate"):
        follower.accept_certificate(replace(certificate, digest="0" * 64))
    assert follower.term == 0
    follower.accept_certificate(certificate)
    stale = LeadershipState(authority)
    stale.submit(vote(authority, "secondary", tick=5))
    different = stale.submit(vote(authority, "tertiary", tick=5))
    with pytest.raises(ProtocolError, match="next term"):
        follower.accept_certificate(different)
