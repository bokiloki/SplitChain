import asyncio

import pytest
import websockets

from splitchain.model import ProtocolError
from splitchain.node import ReferenceNode
from splitchain.replication import ReplicationAuthenticator

SECRET = "a-secure-test-cluster-secret-32-bytes"


def test_replication_envelope_rejects_tampering_and_replay():
    auth = ReplicationAuthenticator(SECRET)
    envelope = auth.sign("primary", 1, {"method": "advance", "params": {"rounds": 1}})
    assert auth.verify(envelope, 0)[1] == 1
    with pytest.raises(ProtocolError, match="replayed"):
        auth.verify(envelope, 1)
    envelope["mutation"]["params"]["rounds"] = 2
    with pytest.raises(ProtocolError, match="replayed"):
        auth.verify(envelope, 0)


def test_primary_replicates_mutation_to_two_real_websocket_nodes(tmp_path):
    async def scenario():
        secondary = ReferenceNode(
            state_path=tmp_path / "secondary.json",
            node_id="secondary",
            role="secondary",
            cluster_secret=SECRET,
        )
        tertiary = ReferenceNode(
            state_path=tmp_path / "tertiary.json",
            node_id="tertiary",
            role="tertiary",
            cluster_secret=SECRET,
        )
        async with (
            websockets.serve(secondary.handler, "127.0.0.1", 0) as secondary_server,
            websockets.serve(tertiary.handler, "127.0.0.1", 0) as tertiary_server,
        ):
            secondary_port = secondary_server.sockets[0].getsockname()[1]
            tertiary_port = tertiary_server.sockets[0].getsockname()[1]
            primary = ReferenceNode(
                state_path=tmp_path / "primary.json",
                node_id="primary",
                role="primary",
                cluster_secret=SECRET,
                peer_urls={
                    "secondary": f"ws://127.0.0.1:{secondary_port}",
                    "tertiary": f"ws://127.0.0.1:{tertiary_port}",
                },
            )
            response = await primary.dispatch({
                "id": 1,
                "method": "offer",
                "params": {"sender": "alice", "receiver": "bob", "value": 10},
            })
            assert "result" in response
            branch_id = response["result"]["branch_id"]
            assert "result" in await primary.dispatch({
                "id": 2,
                "method": "accept",
                "params": {"branch_id": branch_id, "receiver": "bob"},
            })
            assert "result" in await primary.dispatch({
                "id": 3,
                "method": "commit",
                "params": {
                    "branch_id": branch_id,
                    "sender": "alice",
                    "payload": {"amount": 10},
                },
            })
            assert "result" in await primary.dispatch({
                "id": 4, "method": "advance", "params": {"rounds": 3}
            })

        assert primary.ledger.snapshot() == secondary.ledger.snapshot()
        assert primary.ledger.snapshot() == tertiary.ledger.snapshot()
        assert primary.ledger.balances == {"alice": 990, "bob": 1010}
        assert primary.replication_nonces == {"primary": 4}
        assert secondary.replication_nonces == {"primary": 4}

    asyncio.run(scenario())


def test_primary_rejects_mutation_without_quorum():
    async def scenario():
        primary = ReferenceNode(
            node_id="primary", role="primary", cluster_secret=SECRET
        )
        response = await primary.dispatch({
            "id": 1,
            "method": "offer",
            "params": {"sender": "alice", "receiver": "bob", "value": 10},
        })
        assert response["error"]["message"] == "mutation did not receive a 2/3 cluster quorum"
        assert primary.ledger.branches == {}

    asyncio.run(scenario())


def test_replication_nonce_survives_restart(tmp_path):
    async def scenario():
        state = tmp_path / "secondary.json"
        auth = ReplicationAuthenticator(SECRET)
        envelope = auth.sign("primary", 1, {
            "method": "offer",
            "params": {"sender": "alice", "receiver": "bob", "value": 10},
        })
        node = ReferenceNode(
            state_path=state, node_id="secondary", role="secondary", cluster_secret=SECRET
        )
        assert "result" in await node.dispatch({
            "id": 1, "method": "replica.apply", "params": envelope
        })
        restarted = ReferenceNode(
            state_path=state, node_id="secondary", role="secondary", cluster_secret=SECRET
        )
        replay = await restarted.dispatch({
            "id": 2, "method": "replica.apply", "params": envelope
        })
        assert "replayed" in replay["error"]["message"]

    asyncio.run(scenario())


def test_offline_replica_catches_up_from_durable_signed_history(tmp_path):
    async def scenario():
        secondary = ReferenceNode(
            state_path=tmp_path / "secondary.json",
            node_id="secondary", role="secondary", cluster_secret=SECRET,
        )
        async with websockets.serve(secondary.handler, "127.0.0.1", 0) as server:
            secondary_port = server.sockets[0].getsockname()[1]
            primary = ReferenceNode(
                state_path=tmp_path / "primary.json",
                node_id="primary", role="primary", cluster_secret=SECRET,
                peer_urls={
                    "secondary": f"ws://127.0.0.1:{secondary_port}",
                    "tertiary": "ws://127.0.0.1:1",
                },
            )
            response = await primary.dispatch({
                "id": 1, "method": "offer",
                "params": {"sender": "alice", "receiver": "bob", "value": 10},
            })
            assert "result" in response

        restarted_primary = ReferenceNode(
            state_path=tmp_path / "primary.json",
            node_id="primary", role="primary", cluster_secret=SECRET,
        )
        assert len(restarted_primary.replication_log) == 1
        tertiary = ReferenceNode(
            state_path=tmp_path / "tertiary.json",
            node_id="tertiary", role="tertiary", cluster_secret=SECRET,
        )
        async with websockets.serve(tertiary.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            restarted_primary.peer_urls = {"tertiary": f"ws://127.0.0.1:{port}"}
            sync = await restarted_primary.dispatch({
                "id": 2, "method": "cluster.sync", "params": {}
            })
        assert sync["result"] == {"tertiary": "synchronized"}
        assert tertiary.ledger.snapshot() == restarted_primary.ledger.snapshot()
        assert tertiary.replication_nonces == {"primary": 1}

    asyncio.run(scenario())
