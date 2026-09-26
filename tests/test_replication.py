import asyncio
import time
from unittest.mock import patch

import pytest
import websockets

from splitchain.cli import rpc
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


def test_primary_reports_uncertain_outcome_when_commit_ack_is_lost(tmp_path):
    async def scenario():
        secondary = ReferenceNode(
            state_path=tmp_path / "secondary.json", node_id="secondary",
            role="secondary", cluster_secret=SECRET,
        )
        async with websockets.serve(secondary.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            primary = ReferenceNode(
                state_path=tmp_path / "primary.json", node_id="primary",
                role="primary", cluster_secret=SECRET,
                peer_urls={"secondary": f"ws://127.0.0.1:{port}"},
            )
            original_rpc = primary._replica_rpc

            async def lose_commit_ack(url, method, params):
                response = await original_rpc(url, method, params)
                return None if method == "replica.commit" else response

            primary._replica_rpc = lose_commit_ack
            response = await primary.dispatch({
                "id": 1, "method": "offer",
                "params": {"sender": "alice", "receiver": "bob", "value": 10},
            })
            assert response["error"]["code"] == "COMMIT_UNCERTAIN"
            assert primary.ledger.snapshot() == secondary.ledger.snapshot()
            assert primary.replication_nonces == secondary.replication_nonces == {"primary": 1}

    asyncio.run(scenario())


def test_secondary_promotes_with_two_clean_replicas_and_routes_clients(tmp_path):
    async def scenario():
        secondary = ReferenceNode(
            state_path=tmp_path / "secondary.json", node_id="secondary",
            role="secondary", cluster_secret=SECRET,
        )
        tertiary = ReferenceNode(
            state_path=tmp_path / "tertiary.json", node_id="tertiary",
            role="tertiary", cluster_secret=SECRET,
        )
        async with (
            websockets.serve(secondary.handler, "127.0.0.1", 0) as secondary_server,
            websockets.serve(tertiary.handler, "127.0.0.1", 0) as tertiary_server,
        ):
            secondary_url = f"ws://127.0.0.1:{secondary_server.sockets[0].getsockname()[1]}"
            tertiary_url = f"ws://127.0.0.1:{tertiary_server.sockets[0].getsockname()[1]}"
            secondary.peer_urls = {"tertiary": tertiary_url}
            tertiary.peer_urls = {"secondary": secondary_url}
            primary = ReferenceNode(
                state_path=tmp_path / "primary.json", node_id="primary",
                role="primary", cluster_secret=SECRET,
                peer_urls={"secondary": secondary_url, "tertiary": tertiary_url},
            )
            tick = int(time.time() // 2)
            assert "result" in await primary.dispatch({
                "id": 1, "method": "offer",
                "params": {"sender": "alice", "receiver": "bob", "value": 10},
            })
            await primary.failover_step(tick - 4)
            await secondary.failover_step(tick)
            assert secondary.leadership.leader == tertiary.leadership.leader == "secondary"
            assert secondary.leadership.term == tertiary.leadership.term == 1
            old_primary = await primary.dispatch({
                "id": "old", "method": "advance", "params": {"rounds": 1},
            })
            assert "quorum" in old_primary["error"]["message"]
            assert primary._committed_position() == 1
            assert "result" in await primary.dispatch({
                "id": "certificate", "method": "cluster.certificate",
                "params": secondary.leadership.snapshot()["certificates"][-1],
            })
            assert (await primary.dispatch({
                "id": "redirect", "method": "advance", "params": {"rounds": 1},
            }))["error"]["code"] == "NOT_LEADER"
            redirected = await tertiary.dispatch({
                "id": 2, "method": "advance", "params": {"rounds": 1},
            })
            assert redirected["error"]["code"] == "NOT_LEADER"
            assert redirected["error"]["url"] == secondary_url
            prepared_envelope = secondary.replication.sign("secondary", 2, {
                "method": "advance", "params": {"rounds": 1}
            })
            assert "result" in await tertiary.dispatch({
                "id": "prepare", "method": "replica.prepare", "params": prepared_envelope,
            })
            await secondary.failover_step(tick + 1)
            assert tertiary.replication_pending == prepared_envelope
            assert "result" in await tertiary.dispatch({
                "id": "abort", "method": "replica.abort", "params": prepared_envelope,
            })
            assert "result" in await secondary.dispatch({
                "id": 3, "method": "advance", "params": {"rounds": 1},
            })
            assert secondary.ledger.snapshot() == tertiary.ledger.snapshot()
            assert secondary.replication_nonces["secondary"] == 2
            assert len(tertiary.replication_log) == 2
            routed = await rpc(tertiary_url, "advance", {"rounds": 1})
            assert "result" in routed
            assert secondary.ledger.snapshot() == tertiary.ledger.snapshot()
            assert secondary._committed_position() == 3

        restarted = ReferenceNode(
            state_path=tmp_path / "secondary.json", node_id="secondary",
            role="secondary", cluster_secret=SECRET,
        )
        assert restarted.leadership.leader == "secondary"
        assert restarted._committed_position() == 3

    asyncio.run(scenario())


def test_auto_promotion_refuses_a_pending_or_divergent_peer():
    async def scenario():
        secondary = ReferenceNode(node_id="secondary", role="secondary", cluster_secret=SECRET)
        tertiary = ReferenceNode(node_id="tertiary", role="tertiary", cluster_secret=SECRET)
        async with websockets.serve(tertiary.handler, "127.0.0.1", 0) as server:
            secondary.peer_urls = {
                "tertiary": f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            }
            tick = int(time.time() // 2)
            secondary.leadership.last_heartbeat_tick = tick - 4
            tertiary.leadership.last_heartbeat_tick = tick - 4
            envelope = secondary.replication.sign("primary", 1, {
                "method": "advance", "params": {"rounds": 1}
            })
            assert "result" in await tertiary.dispatch({
                "id": 1, "method": "replica.prepare", "params": envelope
            })
            await secondary.failover_step(tick)
            assert secondary.leadership.term == tertiary.leadership.term == 0
            assert "result" in await tertiary.dispatch({
                "id": 2, "method": "replica.abort", "params": envelope
            })
            tertiary.ledger.advance(rounds=1)
            await secondary.failover_step(tick)
            assert secondary.leadership.term == tertiary.leadership.term == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("committed_role", ["secondary", "tertiary"])
def test_takeover_recovers_one_committed_and_one_prepared_replica(
    tmp_path, committed_role
):
    async def scenario():
        secondary = ReferenceNode(
            state_path=tmp_path / "secondary.json", node_id="secondary",
            role="secondary", cluster_secret=SECRET,
        )
        tertiary = ReferenceNode(
            state_path=tmp_path / "tertiary.json", node_id="tertiary",
            role="tertiary", cluster_secret=SECRET,
        )
        envelope = secondary.replication.sign("primary", 1, {
            "method": "advance", "params": {"rounds": 1}
        })
        await secondary.prepare_replica(envelope)
        await tertiary.prepare_replica(envelope)
        await {"secondary": secondary, "tertiary": tertiary}[committed_role].commit_replica(
            envelope
        )
        tick = int(time.time() // 2)
        secondary.leadership.last_heartbeat_tick = tick - 4
        tertiary.leadership.last_heartbeat_tick = tick - 4
        async with websockets.serve(tertiary.handler, "127.0.0.1", 0) as server:
            secondary.peer_urls = {
                "tertiary": f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            }
            await secondary.failover_step(tick)
        assert secondary.leadership.leader == tertiary.leadership.leader == "secondary"
        assert secondary.replication_pending is tertiary.replication_pending is None
        assert secondary.ledger.snapshot() == tertiary.ledger.snapshot()
        assert secondary._committed_position() == tertiary._committed_position() == 1
        for role in ("secondary", "tertiary"):
            restarted = ReferenceNode(
                state_path=tmp_path / f"{role}.json", node_id=role,
                role=role, cluster_secret=SECRET,
            )
            assert restarted.leadership.term == 1

    asyncio.run(scenario())


def test_tertiary_takeover_requires_returned_primary_as_witness(tmp_path):
    async def scenario():
        nodes = {
            role: ReferenceNode(
                state_path=tmp_path / f"{role}.json",
                node_id=role, role=role, cluster_secret=SECRET,
            )
            for role in ("primary", "secondary", "tertiary")
        }
        async with (
            websockets.serve(nodes["primary"].handler, "127.0.0.1", 0) as first,
            websockets.serve(nodes["secondary"].handler, "127.0.0.1", 0) as second,
            websockets.serve(nodes["tertiary"].handler, "127.0.0.1", 0) as third,
        ):
            urls = {
                role: f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
                for role, server in zip(
                    nodes, (first, second, third), strict=True
                )
            }
            for role, node in nodes.items():
                node.peer_urls = {other: url for other, url in urls.items() if other != role}
            tick = int(time.time() // 2)
            await nodes["primary"].failover_step(tick - 4)
            await nodes["secondary"].failover_step(tick)
            await nodes["secondary"].failover_step(tick + 1)
            assert nodes["primary"].leadership.leader == "secondary"
            assert "result" in await nodes["secondary"].dispatch({
                "id": "second-term", "method": "advance", "params": {"rounds": 1},
            })
            with patch("splitchain.node.time.time", return_value=(tick + 5) * 2):
                await nodes["tertiary"].failover_step(tick + 5)
            assert nodes["tertiary"].leadership.leader == "tertiary"
            assert nodes["primary"].leadership.term == 2
            assert nodes["secondary"].leadership.term == 1
            assert "result" in await nodes["tertiary"].dispatch({
                "id": "third-term", "method": "advance", "params": {"rounds": 1},
            })
            assert nodes["tertiary"].ledger.snapshot() == nodes["primary"].ledger.snapshot()

        restarted = ReferenceNode(
            state_path=tmp_path / "primary.json", node_id="primary",
            role="primary", cluster_secret=SECRET,
        )
        assert restarted.leadership.term == 2
        assert restarted._committed_position() == 2

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
        prepared = await node.dispatch({
            "id": 1, "method": "replica.prepare", "params": envelope
        })
        assert prepared["result"]["state"] == "prepared"
        restarted = ReferenceNode(
            state_path=state, node_id="secondary", role="secondary", cluster_secret=SECRET
        )
        assert restarted.replication_pending == envelope
        committed = await restarted.dispatch({
            "id": 2, "method": "replica.commit", "params": envelope
        })
        assert "result" in committed
        restarted = ReferenceNode(
            state_path=state, node_id="secondary", role="secondary", cluster_secret=SECRET
        )
        replay = await restarted.dispatch({
            "id": 3, "method": "replica.prepare", "params": envelope
        })
        assert "replayed" in replay["error"]["message"]

    asyncio.run(scenario())


def test_prepare_does_not_change_ledger_and_abort_survives_restart(tmp_path):
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
        before = node.ledger.snapshot()
        assert "result" in await node.dispatch({
            "id": 1, "method": "replica.prepare", "params": envelope
        })
        assert node.ledger.snapshot() == before
        assert node.replication_pending == envelope
        restarted = ReferenceNode(
            state_path=state, node_id="secondary", role="secondary", cluster_secret=SECRET
        )
        assert "result" in await restarted.dispatch({
            "id": 2, "method": "replica.abort", "params": envelope
        })
        assert restarted.replication_pending is None
        assert restarted.ledger.snapshot() == before

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
