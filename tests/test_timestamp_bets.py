from dataclasses import replace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.membership import StakeMembership
from splitchain.model import ProtocolError
from splitchain.stake_votes import StakeVoteBook
from splitchain.timestamp_bets import (
    TimestampBetBook,
    bet_hash,
    next_secret,
    sign_commit,
    sign_reveal,
    sign_suffix,
)


def setup_bets():
    membership = StakeMembership(0, (("a", 5), ("b", 5), ("observer", 0)), 10)
    private = {name: Ed25519PrivateKey.generate() for name, _ in membership.allocations}
    votes = StakeVoteBook(membership, "test", "genesis", {
        name: key.public_key() for name, key in private.items()
    })
    return TimestampBetBook(votes), private


def commit_for(book, key, voter="a", value=3, secret="x" * 32):
    return sign_commit(
        key, voter=voter, epoch_digest=book.votes.epoch_digest, position=9,
        transaction_digest="tx-a", value=value, target_round=7,
        target_timestamp_ms=1_700_000_000_000,
        commitment=bet_hash(book.votes.epoch_digest, "tx-a", 1_700_000_000_000, secret),
    )


def test_signed_hashes_replay_across_independent_node_sandboxes():
    first, keys = setup_bets()
    second = TimestampBetBook(first.votes)
    signed = commit_for(first, keys["a"])
    for book in (first, second):
        book.commit(signed, observed_round=4)
        assert book.snapshot()["commits"][0]["bet"]["commitment"] == signed.commitment
        assert "x" * 32 not in str(book.snapshot())
    assert first.snapshot() == second.snapshot()
    recovered = TimestampBetBook.from_snapshot(first.votes, second.snapshot())
    reveal = sign_reveal(keys["a"], "a", 9, "x" * 32, first.votes.epoch_digest)
    for book in (first, recovered):
        book.reveal(reveal, observed_round=7)
    assert first.snapshot() == recovered.snapshot()


def test_timestamp_bets_reject_forgery_overstake_replay_and_early_reveal():
    book, keys = setup_bets()
    signed = commit_for(book, keys["a"])
    with pytest.raises(ProtocolError, match="signature"):
        book.commit(commit_for(book, keys["b"]), 4)
    with pytest.raises(ProtocolError, match="underfunded"):
        book.commit(commit_for(book, keys["a"], value=6), 4)
    with pytest.raises(ProtocolError, match="underfunded"):
        book.commit(commit_for(book, keys["observer"], voter="observer"), 4)
    book.commit(signed, 4)
    book.commit(signed, 4)
    with pytest.raises(ProtocolError, match="equivocated"):
        book.commit(commit_for(book, keys["a"], secret="y" * 32), 4)
    reveal = sign_reveal(keys["a"], "a", 9, "x" * 32, book.votes.epoch_digest)
    with pytest.raises(ProtocolError, match="expired"):
        book.reveal(reveal, 6)
    with pytest.raises(ProtocolError, match="expired"):
        book.reveal(reveal, 11)
    with pytest.raises(ProtocolError, match="expired"):
        book.reveal(sign_reveal(keys["a"], "a", 9, "y" * 32, book.votes.epoch_digest), 7)
    book.reveal(reveal, 8)
    altered = book.snapshot()
    altered["commits"][0]["bet"]["transaction_digest"] = "other"
    with pytest.raises(ProtocolError, match="snapshot"):
        TimestampBetBook.from_snapshot(book.votes, altered)


def test_timestamp_bets_reject_stale_epoch():
    book, keys = setup_bets()
    with pytest.raises(ProtocolError, match="underfunded"):
        book.commit(replace(commit_for(book, keys["a"]), epoch_digest="stale"), 4)


def test_revealing_fourth_bet_proves_every_accepted_hash_through_tenth():
    book, keys = setup_bets()
    private_secrets = {}
    secret = "seed-for-ten-accepted-timestamp-bets-0001"
    for index in range(1, 11):
        private_secrets[index] = secret
        target_ms = 1_700_000_000_000 + index
        signed = sign_commit(
            keys["a"], voter="a", epoch_digest=book.votes.epoch_digest,
            position=index, transaction_digest=f"transaction-{index}", value=2,
            target_round=3, target_timestamp_ms=target_ms,
            commitment=bet_hash(book.votes.epoch_digest,
                                f"transaction-{index}", target_ms, secret),
            series_id="ten-bets", sequence_index=index, sequence_length=10,
        )
        book.commit(signed, 0)
        secret = next_secret(secret)
    assert all(value not in str(book.snapshot()) for value in private_secrets.values())
    proof = sign_suffix(keys["a"], "a", book.votes.epoch_digest,
                        "ten-bets", 4, 10, private_secrets[4])
    with pytest.raises(ProtocolError, match="window"):
        book.reveal_suffix(proof, 2)
    book.reveal_suffix(proof, 3)
    assert len(book.suffixes) == 1
    restored = TimestampBetBook.from_snapshot(book.votes, book.snapshot())
    assert restored.snapshot() == book.snapshot()
    assert private_secrets[1] not in str(book.snapshot())
    assert private_secrets[4] in str(book.snapshot())


def test_suffix_rejects_missing_or_mismatched_later_hash_atomically():
    book, keys = setup_bets()
    secret = "seed-for-ten-accepted-timestamp-bets-0001"
    fourth = None
    for index in range(1, 11):
        if index == 4:
            fourth = secret
        target_ms = 1_700_000_000_000 + index
        signed = sign_commit(
            keys["a"], voter="a", epoch_digest=book.votes.epoch_digest,
            position=index, transaction_digest=f"transaction-{index}", value=2,
            target_round=3, target_timestamp_ms=target_ms,
            commitment=bet_hash(book.votes.epoch_digest,
                                f"transaction-{index}", target_ms,
                                "wrong-secret-with-at-least-32-characters" if index == 7 else secret),
            series_id="ten-bets", sequence_index=index, sequence_length=10,
        )
        book.commit(signed, 0)
        secret = next_secret(secret)
    proof = sign_suffix(keys["a"], "a", book.votes.epoch_digest, "ten-bets", 4, 10, fourth)
    with pytest.raises(ProtocolError, match="does not match"):
        book.reveal_suffix(proof, 3)
    assert book.suffixes == {}
    missing = book.snapshot()
    missing["commits"].pop()
    missing["suffixes"].append({"suffix": {
        "voter": proof.voter, "epoch_digest": proof.epoch_digest,
        "series_id": proof.series_id, "start_index": proof.start_index,
        "end_index": proof.end_index, "starting_secret": proof.starting_secret,
        "signature": proof.signature,
    }, "round": 3})
    with pytest.raises(ProtocolError, match="snapshot"):
        TimestampBetBook.from_snapshot(book.votes, missing)
