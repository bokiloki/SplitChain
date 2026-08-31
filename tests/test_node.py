import asyncio
import json

import websockets

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
