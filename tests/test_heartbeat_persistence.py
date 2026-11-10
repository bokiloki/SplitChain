import asyncio
import json

import pytest

from splitchain.model import Ledger, ProtocolError
from splitchain.node import ReferenceNode
from splitchain.persistence import LedgerStore

SECRET = "heartbeat-persistence-test-secret-32-characters"


def test_heartbeat_updates_do_not_rewrite_history_and_survive_restart(tmp_path):
    async def scenario():
        path = tmp_path / "ledger.json"
        node = ReferenceNode(state_path=path, node_id="primary", role="primary",
                             cluster_secret=SECRET)
        node._replica_rpc = no_peer
        await node.failover_step(100)
        before = path.read_bytes()
        stat = path.stat()
        await node.failover_step(101)
        assert path.read_bytes() == before
        assert path.stat().st_mtime_ns == stat.st_mtime_ns
        assert node.store.heartbeat_path.stat().st_size < 256
        restarted = ReferenceNode(state_path=path, node_id="primary", role="primary",
                                  cluster_secret=SECRET)
        assert restarted.leadership.last_heartbeat_tick == 101
        assert restarted.ledger.snapshot() == node.ledger.snapshot()

    asyncio.run(scenario())


async def no_peer(*args):
    return None


def test_replica_heartbeat_is_durable_without_rewriting_checkpoint(tmp_path):
    async def scenario():
        path = tmp_path / "replica.json"
        replica = ReferenceNode(state_path=path, node_id="secondary", role="secondary",
                                cluster_secret=SECRET)
        replica._save()
        before = path.read_bytes()
        heartbeat = replica.leadership.authority.sign_heartbeat("primary", 0, 100, 0)
        await replica.receive_heartbeat(heartbeat)
        assert path.read_bytes() == before
        restarted = ReferenceNode(state_path=path, node_id="secondary", role="secondary",
                                  cluster_secret=SECRET)
        assert restarted.leadership.last_heartbeat_tick == 100

    asyncio.run(scenario())


def test_old_sidecar_cannot_override_new_checkpoint_or_move_tick_back(tmp_path):
    path = tmp_path / "ledger.json"
    store = LedgerStore(path)
    ledger = Ledger({"alice": 100})
    leadership = {"leader": "primary", "term": 0, "committed_nonce": 0,
                  "last_heartbeat_tick": 100}
    store.save(ledger, leadership=leadership)
    store.save_heartbeat({"last_heartbeat_tick": 110})
    assert LedgerStore(path).load_leadership_snapshot()["last_heartbeat_tick"] == 110
    leadership["last_heartbeat_tick"] = 120
    store.save(ledger, leadership=leadership)
    assert LedgerStore(path).load_leadership_snapshot()["last_heartbeat_tick"] == 120
    leadership.update(term=1, leader="secondary", last_heartbeat_tick=50)
    store.save(ledger, leadership=leadership)
    assert LedgerStore(path).load_leadership_snapshot()["last_heartbeat_tick"] == 50


def test_pending_and_ledger_changes_invalidate_old_sidecar(tmp_path):
    path = tmp_path / "ledger.json"
    store = LedgerStore(path)
    ledger = Ledger({"alice": 100})
    leadership = {"leader": "primary", "term": 0, "committed_nonce": 0,
                  "last_heartbeat_tick": 100}
    store.save(ledger, leadership=leadership)
    store.save_heartbeat({"last_heartbeat_tick": 110})
    store.save(ledger, leadership=leadership, replication_pending={"nonce": 1})
    assert LedgerStore(path).load_leadership_snapshot()["last_heartbeat_tick"] == 100


def test_corrupt_sidecar_fails_closed(tmp_path):
    path = tmp_path / "ledger.json"
    store = LedgerStore(path)
    store.save(Ledger({"alice": 100}), leadership={"last_heartbeat_tick": 1})
    store.heartbeat_path.write_text(json.dumps({"schema": "unexpected"}))
    with pytest.raises(ProtocolError, match="heartbeat"):
        LedgerStore(path).load_leadership_snapshot()


def test_failed_checkpoint_write_does_not_update_heartbeat_anchor(tmp_path, monkeypatch):
    store = LedgerStore(tmp_path / "ledger.json")
    ledger = Ledger({"alice": 100})
    store.save(ledger, leadership={"last_heartbeat_tick": 1})
    anchor = store._heartbeat_anchor

    def fail(*args):
        raise ProtocolError("simulated write failure")

    monkeypatch.setattr(store, "_atomic_write", fail)
    with pytest.raises(ProtocolError):
        store.save(ledger, replication_pending={"nonce": 1})
    assert store._heartbeat_anchor == anchor
