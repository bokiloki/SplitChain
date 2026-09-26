import asyncio
import json

import websockets

from splitchain import round_clock
from splitchain.auth import RequestAuthenticator
from splitchain.node import ReferenceNode
from splitchain.round_clock import RoundClock


def test_round_driver_signs_rounds_and_persists_monotonic_nonce(tmp_path, monkeypatch):
    async def scenario():
        secret = "operator-secret-at-least-32-characters"
        node = ReferenceNode(auth_secrets={"testnet_operator": secret})
        async with websockets.serve(node.handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            monkeypatch.setattr(round_clock, "BACKENDS", (url,))
            path = tmp_path / "round-nonce"
            clock = RoundClock(secret, path)
            first = clock.next_request()
            assert RequestAuthenticator.sign(first, "testnet_operator",
                                             first["auth"]["nonce"], secret) == first["auth"]
            assert "result" in await clock.step()
            restarted = RoundClock(secret, path)
            assert "result" in await restarted.step()
            assert node.ledger.round == 2
            assert restarted.last_nonce > first["auth"]["nonce"]
            assert int(path.read_text()) == restarted.last_nonce
            assert json.loads(json.dumps(node.ledger.snapshot()))["round"] == 2

    asyncio.run(scenario())
