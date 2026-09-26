import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.membership import StakeMembership
from splitchain.model import GenesisConfig, Ledger, ProtocolError
from splitchain.stake_vault import SandboxConsensusStore
from splitchain.stake_votes import StakeDecision, StakeVoteBook, sign_vote
from splitchain.timestamp_bets import TimestampBetBook, bet_hash, sign_commit


def test_each_validator_restores_independent_full_consensus_state(tmp_path):
    genesis = GenesisConfig.from_dict({
        "schema": "splitchain-genesis/v1", "network_id": "sandbox-test", "max_supply": 21,
        "allocations": {"faucet": 14, "reserve": 7}, "locked_accounts": ["reserve"],
    })
    membership = StakeMembership.from_locked_reserve(
        Ledger(genesis=genesis), "reserve", 0,
        (("a", 3), ("b", 2), ("c", 2), ("candidate", 0)),
    )
    private = {name: Ed25519PrivateKey.generate() for name, _ in membership.allocations}
    public = {name: key.public_key() for name, key in private.items()}
    ledger = Ledger(genesis=genesis)
    book = StakeVoteBook(membership, genesis.network_id, genesis.digest(), public)
    target = StakeDecision(genesis.network_id, 0, book.epoch_digest,
                           "mutation", 1, "signed-offer", 2)
    for name in ("a", "b", "c"):
        book.submit(sign_vote(private[name], name, target))
    book.certificate(target).verify(book)
    bets = TimestampBetBook(book)
    signed_bet = sign_commit(
        private["a"], voter="a", epoch_digest=book.epoch_digest, position=2,
        transaction_digest="bet-tx", value=2, target_round=3,
        target_timestamp_ms=1_700_000_000_000,
        commitment=bet_hash(book.epoch_digest, "bet-tx", 1_700_000_000_000, "x" * 32),
    )
    bets.commit(signed_bet, 0)
    stores = [SandboxConsensusStore(tmp_path / name / "consensus.json", genesis=genesis,
                                    membership=membership, keys=public) for name in ("a", "b", "c")]
    for store in stores:
        store.save(ledger, book, 1, bets)
    for store in stores:
        recovered, votes, position, bets, clock = store.load()
        assert recovered.snapshot() == ledger.snapshot()
        assert position == 1
        votes.certificate(target).verify(votes)
        assert bets.commits[("a", 2)][0].commitment == signed_bet.commitment
        assert clock.latest == {}
        assert "x" * 32 not in store.path.read_text()
    modified = json.loads(stores[0].path.read_text())
    modified["position"] = 2
    stores[0].path.write_text(json.dumps(modified))
    with pytest.raises(ProtocolError, match="checkpoint"):
        stores[0].load()
    for store in stores[1:]:
        assert store.load()[2] == 1


def test_sandbox_rejects_stale_epoch_and_public_directory(tmp_path):
    genesis = GenesisConfig.from_dict({
        "schema": "splitchain-genesis/v1", "network_id": "sandbox-test", "max_supply": 21,
        "allocations": {"faucet": 14, "reserve": 7}, "locked_accounts": ["reserve"],
    })
    old = StakeMembership(0, (("a", 7),), 7)
    new = StakeMembership(1, (("a", 7),), 7)
    key = Ed25519PrivateKey.generate().public_key()
    book = StakeVoteBook(old, genesis.network_id, genesis.digest(), {"a": key})
    store = SandboxConsensusStore(tmp_path / "private" / "consensus.json", genesis=genesis,
                                  membership=new, keys={"a": key})
    with pytest.raises(ProtocolError, match="another stake epoch"):
        store.save(Ledger(genesis=genesis), book, 0)
    exposed = tmp_path / "exposed"
    exposed.mkdir(mode=0o755)
    store = SandboxConsensusStore(exposed / "consensus.json", genesis=genesis,
                                  membership=old, keys={"a": key})
    with pytest.raises(ProtocolError, match="private"):
        store.save(Ledger(genesis=genesis), book, 0)
