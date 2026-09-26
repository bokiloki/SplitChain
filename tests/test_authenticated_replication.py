import asyncio

import websockets

from splitchain.auth import RequestAuthenticator
from splitchain.model import GenesisConfig
from splitchain.node import ReferenceNode

SECRETS = {
    "testnet_faucet": "faucet-only-secret-at-least-32-characters",
    "testnet_operator": "operator-only-secret-at-least-32-characters",
    "bob": "bob-only-secret-at-least-32-characters",
}
CLUSTER_SECRET = "cluster-secret-at-least-32-characters"


def signed(method, params, actor, nonce):
    request = {"id": f"{actor}-{nonce}", "method": method, "params": params}
    request["auth"] = RequestAuthenticator.sign(request, actor, nonce, SECRETS[actor])
    return request


def test_signed_test_tokens_replicate_and_replay_is_fenced_on_restart(tmp_path):
    async def scenario():
        genesis = GenesisConfig.from_dict({
            "schema": "splitchain-genesis/v1", "network_id": "test-sandbox",
            "max_supply": 21_000_000,
            "allocations": {"testnet_faucet": 14_000_000, "testnet_locked_reserve": 7_000_000},
            "locked_accounts": ["testnet_locked_reserve"],
        })
        secondary_path = tmp_path / "secondary.json"
        secondary = ReferenceNode(state_path=secondary_path, genesis=genesis,
                                  auth_secrets=SECRETS, node_id="secondary",
                                  role="secondary", cluster_secret=CLUSTER_SECRET)
        async with websockets.serve(secondary.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            primary = ReferenceNode(state_path=tmp_path / "primary.json", genesis=genesis,
                                    auth_secrets=SECRETS, node_id="primary", role="primary",
                                    cluster_secret=CLUSTER_SECRET,
                                    peer_urls={"secondary": f"ws://127.0.0.1:{port}"})
            offer = signed("offer", {"sender": "testnet_faucet", "receiver": "bob",
                                     "value": 10}, "testnet_faucet", 1)
            invalid = signed("offer", {"sender": "testnet_faucet", "receiver": "bob",
                                       "value": 20_000_000}, "testnet_faucet", 1)
            assert (await primary.dispatch(invalid))["error"]
            assert (await primary.dispatch({"id": "unsigned", "method": "offer",
                                            "params": offer["params"]}))["error"]
            result = await primary.dispatch(offer)
            branch_id = result["result"]["branch_id"]
            assert (await primary.dispatch(offer))["error"]["message"] == "request nonce was already used"
            assert "result" in await primary.dispatch(signed(
                "accept", {"branch_id": branch_id, "receiver": "bob"}, "bob", 1))
            assert "result" in await primary.dispatch(signed(
                "commit", {"branch_id": branch_id, "sender": "testnet_faucet",
                           "payload": {"memo": "sandbox"}}, "testnet_faucet", 2))
            assert "result" in await primary.dispatch(signed(
                "advance", {"rounds": 3}, "testnet_operator", 1))
            assert primary.ledger.snapshot() == secondary.ledger.snapshot()
            assert primary.ledger.balances["bob"] == 10
            assert primary.ledger.balances["testnet_locked_reserve"] == 7_000_000
        restarted = ReferenceNode(state_path=secondary_path, genesis=genesis,
                                  auth_secrets=SECRETS, node_id="secondary",
                                  role="secondary", cluster_secret=CLUSTER_SECRET)
        assert restarted.authenticator.snapshot() == {
            "bob": 1, "testnet_faucet": 2, "testnet_operator": 1,
        }
        assert restarted.ledger.balances["bob"] == 10

    asyncio.run(scenario())
