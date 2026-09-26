import asyncio
import os
import socket as socket_module

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.membership import StakeMembership
from splitchain.model import GenesisConfig, ProtocolError
from splitchain.sandbox_daemon import SandboxDaemon, sandbox_request
from splitchain.stake_vault import SandboxConsensusStore
from splitchain.stake_votes import StakeDecision, sign_vote
from splitchain.timestamp_bets import bet_hash, sign_commit, sign_suffix


def test_local_sandbox_accepts_only_signed_events_and_never_lists_secrets(tmp_path):
    try:
        probe = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    except PermissionError:
        pytest.skip("this executor does not permit Unix sockets")
    else:
        probe.close()

    async def scenario():
        genesis = GenesisConfig.from_dict({
            "schema": "splitchain-genesis/v1", "network_id": "sandbox-test",
            "max_supply": 21, "allocations": {"faucet": 14, "reserve": 7},
            "locked_accounts": ["reserve"],
        })
        membership = StakeMembership(0, (("a", 7), ("observer", 0)), 7)
        signer = Ed25519PrivateKey.generate()
        keys = {"a": signer.public_key(),
                "observer": Ed25519PrivateKey.generate().public_key()}
        store = SandboxConsensusStore(tmp_path / "state" / "consensus.json",
                                      genesis=genesis, membership=membership, keys=keys)
        socket = tmp_path / "sandbox.sock"
        daemon = SandboxDaemon(socket, store)
        service = asyncio.create_task(daemon.serve())
        try:
            for _ in range(100):
                if socket.exists():
                    break
                await asyncio.sleep(0.01)
            assert socket.exists()
            assert os.stat(socket).st_mode & 0o777 == 0o600
            health = await sandbox_request(socket, "health")
            secret = "private-origin-bet-secret-32-characters"
            bet = sign_commit(
                signer, voter="a", epoch_digest=health["epoch_digest"],
                position=1, transaction_digest="tx", value=3, target_round=3,
                target_timestamp_ms=1_700_000_000_000,
                commitment=bet_hash(health["epoch_digest"], "tx", 1_700_000_000_000,
                                    secret),
            )
            assert (await sandbox_request(socket, "bet.commit", bet, 0))["stored"]
            with pytest.raises(ProtocolError, match="unknown sandbox operation"):
                await sandbox_request(socket, "bets.list")
            with pytest.raises(ProtocolError, match="invalid timestamp bet signature"):
                await sandbox_request(socket, "bet.commit", sign_commit(
                    Ed25519PrivateKey.generate(), voter="a", epoch_digest=health["epoch_digest"],
                    position=2, transaction_digest="tx", value=3, target_round=3,
                    target_timestamp_ms=1_700_000_000_000,
                    commitment=bet_hash(health["epoch_digest"], "tx", 1_700_000_000_000,
                                        secret)), 0)
            assert (await sandbox_request(socket, "health"))["commit_count"] == 1
            assert secret not in store.path.read_text()
        finally:
            service.cancel()
            with pytest.raises(asyncio.CancelledError):
                await service

    asyncio.run(scenario())


def test_sandbox_dispatch_verifies_commit_and_omits_history_queries(tmp_path):
    genesis = GenesisConfig.from_dict({
        "schema": "splitchain-genesis/v1", "network_id": "sandbox-test",
        "max_supply": 21, "allocations": {"faucet": 14, "reserve": 7},
        "locked_accounts": ["reserve"],
    })
    membership = StakeMembership(0, (("a", 7),), 7)
    key = Ed25519PrivateKey.generate()
    store = SandboxConsensusStore(tmp_path / "state" / "consensus.json",
                                  genesis=genesis, membership=membership,
                                  keys={"a": key.public_key()})
    daemon = SandboxDaemon(tmp_path / "sandbox.sock", store)
    secret = "private-origin-bet-secret-32-characters"
    bet = sign_commit(
        key, voter="a", epoch_digest=daemon.votes.epoch_digest,
        position=1, transaction_digest="tx", value=3, target_round=3,
        target_timestamp_ms=1_700_000_000_000,
        commitment=bet_hash(daemon.votes.epoch_digest, "tx", 1_700_000_000_000, secret),
    )
    async def scenario():
        assert (await daemon.dispatch({"method": "bet.commit", "event": vars(bet),
                                       "round": 0}))["stored"]
        with pytest.raises(ProtocolError, match="locally verified ledger"):
            await daemon.dispatch({"method": "bet.commit", "event": vars(bet), "round": 3})
        with pytest.raises(ProtocolError, match="unknown sandbox operation"):
            await daemon.dispatch({"method": "bets.list"})
        assert secret not in store.path.read_text()
        for expected_round in (1, 2, 3):
            challenge = StakeDecision(**await daemon.dispatch({"method": "round.challenge"}))
            vote = sign_vote(key, "a", challenge)
            response = await daemon.dispatch({"method": "round.advance.vote", "event": {
                "voter": vote.voter, "decision": vars(vote.decision),
                "signature": vote.signature,
            }})
            assert response == {"quorum": True, "round": expected_round}
        suffix = sign_suffix(key, "a", daemon.votes.epoch_digest, "single", 1, 1, secret)
        assert (await daemon.dispatch({"method": "bet.reveal_suffix",
                                       "event": vars(suffix), "round": 3}))["verified"]

    asyncio.run(scenario())
    assert daemon.bets.commits[("a", 1)][0].commitment == bet.commitment
    assert SandboxDaemon(tmp_path / "sandbox.sock", store).ledger.round == 3
    assert secret in store.path.read_text()
