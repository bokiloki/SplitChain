import asyncio
import json

import websockets

from splitchain import public_rpc, round_clock
from splitchain.auth import RequestAuthenticator
from splitchain.model import GenesisConfig
from splitchain.node import ReferenceNode


def test_public_gateway_filters_methods_and_keeps_internal_leader_url_private(monkeypatch):
    async def scenario():
        seen = []

        async def backend(socket):
            request = json.loads(await socket.recv())
            seen.append(request)
            await socket.send(json.dumps({"id": request["id"], "result": {"branch_id": "abc"}}))

        async with websockets.serve(backend, "127.0.0.1", 0) as node_server:
            node_url = f"ws://127.0.0.1:{node_server.sockets[0].getsockname()[1]}"
            monkeypatch.setattr(public_rpc, "BACKENDS", (node_url,))
            gateway = public_rpc.Gateway()
            async with websockets.serve(gateway.handler, "127.0.0.1", 0) as server:
                url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/rpc"

                async def call(request):
                    async with websockets.connect(url) as socket:
                        await socket.send(json.dumps(request))
                        return json.loads(await socket.recv())

                for method in ("advance", "replica.commit", "cluster.sync"):
                    assert (await call({"method": method, "params": {}, "auth": {}}))["error"]
                assert (await call({"method": "offer", "params": {}}))["error"]
                accepted = await call({"id": 1, "method": "offer", "params": {
                    "sender": "alice", "receiver": "bob", "value": 1,
                }, "auth": {"actor": "alice", "nonce": 1, "signature": "signed"}})
                assert accepted["result"]["branch_id"] == "abc"
                assert len(seen) == 1

    asyncio.run(scenario())


def test_gateway_to_three_replicas_finalizes_sandbox_units(monkeypatch, tmp_path):
    async def scenario():
        secrets = {actor: (actor + "-" * 36) for actor in (
            "testnet_faucet", "bob", "testnet_operator",
        )}
        genesis = GenesisConfig.from_dict({
            "schema": "splitchain-genesis/v1", "network_id": "public-sandbox-test",
            "max_supply": 21_000_000,
            "allocations": {"testnet_faucet": 14_000_000, "testnet_locked_reserve": 7_000_000},
            "locked_accounts": ["testnet_locked_reserve"],
        })
        cluster_secret = "cluster-secret-at-least-32-characters"
        secondary = ReferenceNode(state_path=tmp_path / "secondary", genesis=genesis,
                                  auth_secrets=secrets, node_id="secondary",
                                  role="secondary", cluster_secret=cluster_secret)
        tertiary = ReferenceNode(state_path=tmp_path / "tertiary", genesis=genesis,
                                 auth_secrets=secrets, node_id="tertiary",
                                 role="tertiary", cluster_secret=cluster_secret)
        async with (websockets.serve(secondary.handler, "127.0.0.1", 0) as s_server,
                    websockets.serve(tertiary.handler, "127.0.0.1", 0) as t_server):
            s_url = f"ws://127.0.0.1:{s_server.sockets[0].getsockname()[1]}"
            t_url = f"ws://127.0.0.1:{t_server.sockets[0].getsockname()[1]}"
            primary = ReferenceNode(state_path=tmp_path / "primary", genesis=genesis,
                                    auth_secrets=secrets, node_id="primary",
                                    role="primary", cluster_secret=cluster_secret,
                                    peer_urls={"secondary": s_url, "tertiary": t_url})
            async with websockets.serve(primary.handler, "127.0.0.1", 0) as p_server:
                p_url = f"ws://127.0.0.1:{p_server.sockets[0].getsockname()[1]}"
                monkeypatch.setattr(public_rpc, "BACKENDS", (p_url, s_url, t_url))
                monkeypatch.setattr(round_clock, "BACKENDS", (p_url, s_url, t_url))
                gateway = public_rpc.Gateway()
                async with websockets.serve(gateway.handler, "127.0.0.1", 0) as g_server:
                    g_url = f"ws://127.0.0.1:{g_server.sockets[0].getsockname()[1]}/rpc"

                    async def signed(method, params, actor, nonce):
                        request = {"id": f"{actor}-{nonce}", "method": method, "params": params}
                        request["auth"] = RequestAuthenticator.sign(
                            request, actor, nonce, secrets[actor])
                        return request

                    async def public(request):
                        async with websockets.connect(g_url) as socket:
                            await socket.send(json.dumps(request))
                            return json.loads(await socket.recv())

                    offer = await public(await signed("offer", {
                        "sender": "testnet_faucet", "receiver": "bob", "value": 10,
                    }, "testnet_faucet", 1))
                    branch = offer["result"]["branch_id"]
                    assert "result" in await public(await signed("accept", {
                        "branch_id": branch, "receiver": "bob",
                    }, "bob", 1))
                    assert "result" in await public(await signed("commit", {
                        "branch_id": branch, "sender": "testnet_faucet", "payload": {},
                    }, "testnet_faucet", 2))
                    clock = round_clock.RoundClock(secrets["testnet_operator"],
                                                  tmp_path / "round-nonce")
                    for _ in range(3):
                        assert "result" in await clock.step()
                    assert all(node.ledger.balances["bob"] == 10 for node in (
                        primary, secondary, tertiary,
                    ))
                    assert primary.ledger.snapshot() == secondary.ledger.snapshot()
                    assert primary.ledger.snapshot() == tertiary.ledger.snapshot()

    asyncio.run(scenario())
