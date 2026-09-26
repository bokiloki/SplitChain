import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.bet_relay import BetPeerRelay
from splitchain.model import ProtocolError
from splitchain.timestamp_bets import bet_hash, sign_commit


def test_peer_relay_forwards_only_sandbox_verified_signed_commitments(monkeypatch, tmp_path):
    async def scenario():
        relay = BetPeerRelay(
            "primary", tmp_path / "sandbox.sock",
            {"secondary": "wss://secondary:8765", "tertiary": "wss://tertiary:8765"},
            registry=object(), tls=object(),
        )
        signer = Ed25519PrivateKey.generate()
        bet = sign_commit(
            signer, voter="primary", epoch_digest="epoch", position=1,
            transaction_digest="tx", value=1, target_round=3,
            target_timestamp_ms=1_700_000_000_000,
            commitment=bet_hash("epoch", "tx", 1_700_000_000_000, "x" * 32),
        )
        verified = AsyncMock(return_value={"stored": True})
        sent = AsyncMock()
        monkeypatch.setattr("splitchain.bet_relay.sandbox_request", verified)
        monkeypatch.setattr(relay, "_send", sent)
        assert (await relay.publish(bet, 0)) == {"stored": True}
        assert sent.await_count == 2
        for call in sent.await_args_list:
            assert call.args[1]["event"]["commitment"] == bet.commitment
            assert "x" * 32 not in json.dumps(call.args[1])
        await relay.publish(bet, 0)
        assert sent.await_count == 2
        verified.side_effect = ProtocolError("sandbox rejected signature")
        with pytest.raises(ProtocolError, match="sandbox rejected"):
            await relay.publish(sign_commit(
                Ed25519PrivateKey.generate(), voter="primary", epoch_digest="epoch",
                position=2, transaction_digest="tx", value=1, target_round=3,
                target_timestamp_ms=1_700_000_000_000,
                commitment=bet_hash("epoch", "tx", 1_700_000_000_000, "y" * 32),
            ), 0)
        assert sent.await_count == 2

    asyncio.run(scenario())


def test_peer_relay_restores_durable_retry_checkpoint(tmp_path):
    pending = tmp_path / "state" / "relay-pending.json"
    message = {"method": "clock.heartbeat", "event": {"voter": "a"}}
    relay = BetPeerRelay("primary", tmp_path / "sandbox.sock",
                         {"secondary": "wss://secondary:8765"}, object(), object(), pending)
    relay.pending[("secondary", "digest")] = message
    relay._save_pending()
    restored = BetPeerRelay("primary", tmp_path / "sandbox.sock",
                            {"secondary": "wss://secondary:8765"}, object(), object(), pending)
    assert restored.pending == relay.pending
    assert pending.stat().st_mode & 0o077 == 0
