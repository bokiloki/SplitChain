import json

import pytest

from splitchain.model import GenesisConfig, Ledger, ProtocolError
from splitchain.persistence import LedgerStore


def config():
    return GenesisConfig.from_dict({
        "schema": "splitchain-genesis/v1", "network_id": "testnet-1",
        "max_supply": 21_000_000,
        "allocations": {"faucet": 14_000_000, "reserve": 7_000_000},
        "locked_accounts": ["reserve"],
    })


def test_genesis_reserve_and_supply_restore(tmp_path):
    genesis = config()
    store = LedgerStore(tmp_path / "state.json")
    ledger, _, _ = store.load_full_node_state({}, genesis)
    with pytest.raises(ProtocolError, match="locked account"):
        ledger.offer("reserve", "faucet", 1)
    ledger.register_account("user")
    branch = ledger.offer("faucet", "user", 10)
    ledger.accept(branch.branch_id, "user")
    ledger.commit(branch.branch_id, "faucet", {"test": 1})
    ledger.advance(3)
    store.save(ledger)
    restored, _, _ = store.load_full_node_state({}, genesis)
    assert restored.balances == {"faucet": 13_999_990, "reserve": 7_000_000, "user": 10}
    assert restored.canonical_history[0] == genesis.digest()
    with pytest.raises(ProtocolError, match="differs"):
        store.load_full_node_state({}, GenesisConfig.from_dict({**genesis.public(), "network_id": "other"}))


def test_snapshot_supply_and_reserve_tampering():
    ledger = Ledger(genesis=config())
    snapshot = ledger.snapshot()
    snapshot["balances"]["faucet"] += 1
    with pytest.raises(ProtocolError, match="supply"):
        Ledger.from_snapshot(snapshot, config())
    snapshot = ledger.snapshot()
    snapshot["balances"]["reserve"] -= 1
    snapshot["balances"]["faucet"] += 1
    with pytest.raises(ProtocolError, match="reserve"):
        Ledger.from_snapshot(snapshot, config())
    snapshot = ledger.snapshot()
    snapshot["canonical_history"]["0"] = "0" * 64
    with pytest.raises(ProtocolError, match="genesis digest"):
        Ledger.from_snapshot(snapshot, config())


def test_reject_invalid_allocations_and_locked_accounts():
    raw = config().public()
    for change in ({"max_supply": 1}, {"allocations": {"x": True}}, {"locked_accounts": ["missing"]}):
        with pytest.raises(ProtocolError):
            GenesisConfig.from_dict({**raw, **change})


def test_published_candidate_config():
    from pathlib import Path

    raw = json.loads((Path(__file__).parents[1] / "configs/testnet-genesis.json").read_text())
    genesis = GenesisConfig.from_dict(raw)
    assert sum(genesis.allocations.values()) == genesis.max_supply == 21_000_000
    assert sum(genesis.allocations[account] for account in genesis.locked_accounts) == 7_000_000
