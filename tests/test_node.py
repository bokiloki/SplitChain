import asyncio
import json

import websockets

from splitchain.auth import RequestAuthenticator
from splitchain.node import ReferenceNode


def test_rpc_dispatch_flow():
    async def scenario():
        node = ReferenceNode({"alice": 100, "bob": 0})
        offered = await node.dispatch({"id": 1, "method": "offer", "params": {
            "sender": "alice", "receiver": "bob", "value": 10
        }})
        branch_id = offered["result"]["branch_id"]
        accepted = await node.dispatch({"id": 2, "method": "accept", "params": {
            "branch_id": branch_id, "receiver": "bob"
        }})
        assert accepted["result"]["state"] == "accepted"
    asyncio.run(scenario())


def test_node_recovers_persisted_state(tmp_path):
    async def scenario():
        state = tmp_path / "ledger.json"
        node = ReferenceNode({"alice": 100, "bob": 0}, state_path=state)
        offered = await node.dispatch({"id": 1, "method": "offer", "params": {
            "sender": "alice", "receiver": "bob", "value": 10
        }})
        restarted = ReferenceNode({"alice": 999}, state_path=state)
        assert restarted.ledger.branches[offered["result"]["branch_id"]].sender == "alice"
        assert restarted.ledger.balances["alice"] == 100
    asyncio.run(scenario())


def test_authenticated_rpc_rejects_replay():
    async def scenario():
        node = ReferenceNode(auth_secrets={"alice": "test-secret"})
        request = {"id": 1, "method": "offer", "params": {
            "sender": "alice", "receiver": "bob", "value": 10
        }}
        request["auth"] = RequestAuthenticator.sign(request, "alice", 1, "test-secret")
        accepted = await node.dispatch(request)
        replayed = await node.dispatch(request)
        assert "result" in accepted
        assert replayed["error"]["message"] == "request nonce was already used"
    asyncio.run(scenario())


def test_authenticated_rpc_rejects_modified_payload():
    async def scenario():
        node = ReferenceNode(auth_secrets={"alice": "test-secret"})
        request = {"id": 1, "method": "offer", "params": {
            "sender": "alice", "receiver": "bob", "value": 10
        }}
        request["auth"] = RequestAuthenticator.sign(request, "alice", 1, "test-secret")
        request["params"]["value"] = 11
        rejected = await node.dispatch(request)
        assert rejected["error"]["message"] == "invalid request signature"
    asyncio.run(scenario())


def test_authenticated_rpc_binds_actor_to_protocol_participant():
    async def scenario():
        node = ReferenceNode(auth_secrets={"mallory": "test-secret"})
        request = {"id": 1, "method": "offer", "params": {
            "sender": "alice", "receiver": "bob", "value": 10
        }}
        request["auth"] = RequestAuthenticator.sign(request, "mallory", 1, "test-secret")
        rejected = await node.dispatch(request)
        assert rejected["error"]["message"] == (
            "authenticated actor does not match request participant"
        )
    asyncio.run(scenario())


def test_replay_nonce_survives_restart(tmp_path):
    async def scenario():
        state = tmp_path / "node.json"
        request = {"id": 1, "method": "offer", "params": {
            "sender": "alice", "receiver": "bob", "value": 10
        }}
        request["auth"] = RequestAuthenticator.sign(request, "alice", 7, "test-secret")
        first = ReferenceNode(
            {"alice": 100, "bob": 0},
            state_path=state,
            auth_secrets={"alice": "test-secret"},
        )
        assert "result" in await first.dispatch(request)
        restarted = ReferenceNode(state_path=state, auth_secrets={"alice": "test-secret"})
        replayed = await restarted.dispatch(request)
        assert replayed["error"]["message"] == "request nonce was already used"
    asyncio.run(scenario())


def test_real_websocket_round_trip():
    async def scenario():
        node = ReferenceNode({"alice": 100, "bob": 0})
        async with websockets.serve(node.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}") as socket:
                await socket.send(json.dumps({"id": "smoke", "method": "status", "params": {}}))
                response = json.loads(await socket.recv())
                assert response["id"] == "smoke"
                assert response["result"]["balances"] == {"alice": 100, "bob": 0}

    asyncio.run(scenario())
