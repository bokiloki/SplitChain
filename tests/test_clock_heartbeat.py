import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.clock_heartbeat import ClockTracker, sign_heartbeat
from splitchain.membership import StakeMembership
from splitchain.model import ProtocolError
from splitchain.stake_votes import StakeVoteBook


def test_heartbeats_report_weighted_alignment_and_clock_skew_without_advancing_round():
    membership = StakeMembership(0, (("a", 3), ("b", 2), ("c", 2), ("observer", 0)), 7)
    private = {name: Ed25519PrivateKey.generate() for name, _ in membership.allocations}
    book = StakeVoteBook(membership, "net", "genesis", {
        name: key.public_key() for name, key in private.items()
    })
    tracker = ClockTracker(book, max_skew_ms=100)
    for name in ("a", "b", "observer"):
        heartbeat = sign_heartbeat(
            private[name], voter=name, epoch_digest=book.epoch_digest,
            sequence=1, round_number=10, ledger_digest="block-head",
            sent_at_ms=10_000,
        )
        assert not tracker.observe(heartbeat, 10_020)
    assert tracker.observed_stake(10, "block-head") == 5
    assert tracker.observed_stake(11, "block-head") == 0
    assert tracker.observed_stake(10, "other-head") == 0
    c = sign_heartbeat(private["c"], voter="c", epoch_digest=book.epoch_digest,
                       sequence=1, round_number=10, ledger_digest="block-head",
                       sent_at_ms=11_000)
    assert tracker.observe(c, 10_020)
    assert tracker.observed_stake(10, "block-head") == 5
    restored = ClockTracker.from_snapshot(book, tracker.snapshot())
    assert restored.snapshot() == tracker.snapshot()
    assert tracker.observe(c, 10_021)
    with pytest.raises(ProtocolError, match="replayed"):
        tracker.observe(sign_heartbeat(
            private["c"], voter="c", epoch_digest=book.epoch_digest,
            sequence=1, round_number=10, ledger_digest="another",
            sent_at_ms=11_000,
        ), 10_021)
    with pytest.raises(ProtocolError, match="signature"):
        tracker.observe(sign_heartbeat(
            private["b"], voter="a", epoch_digest=book.epoch_digest,
            sequence=2, round_number=10, ledger_digest="block-head",
            sent_at_ms=10_000,
        ), 10_020)
