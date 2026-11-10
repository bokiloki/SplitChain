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
        async def handler(socket):
            async for raw in socket:
                request = json.loads(raw)
                if request["method"] == "cluster.leadership":
                    response = {"result": {"term": 0, "leader": "primary"}}
                else:
                    response = await node.dispatch(request)
                await socket.send(json.dumps(response))

        async with websockets.serve(handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            monkeypatch.setattr(round_clock, "BACKENDS", {"primary": url})
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


def test_round_driver_targets_newer_leader_and_does_not_replay_unknown_outcome(tmp_path, monkeypatch):
    async def scenario():
        attempts = []

        async def stale_primary(socket):
            request = json.loads(await socket.recv())
            assert request["method"] == "cluster.leadership"
            await socket.send(json.dumps({"result": {"term": 0, "leader": "primary"}}))

        async def promoted_secondary(socket):
            request = json.loads(await socket.recv())
            if request["method"] == "cluster.leadership":
                await socket.send(json.dumps({"result": {"term": 1, "leader": "secondary"}}))
            else:
                attempts.append(request)
                await socket.close(code=1011)

        async with (
            websockets.serve(stale_primary, "127.0.0.1", 0) as primary,
            websockets.serve(promoted_secondary, "127.0.0.1", 0) as secondary,
        ):
            monkeypatch.setattr(round_clock, "BACKENDS", {
                "primary": f"ws://127.0.0.1:{primary.sockets[0].getsockname()[1]}",
                "secondary": f"ws://127.0.0.1:{secondary.sockets[0].getsockname()[1]}",
            })
            clock = RoundClock("operator-secret-at-least-32-characters", tmp_path / "round-nonce")
            result = await clock.step()
            assert result["error"]["code"] == "COMMIT_UNCERTAIN"
            assert [request["method"] for request in attempts] == ["advance"]

    asyncio.run(scenario())
