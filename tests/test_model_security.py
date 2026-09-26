import pytest

from splitchain.model import GenesisConfig, Ledger, ProtocolError


def test_offer_rejects_unknown_receiver_without_locking_funds():
    genesis = GenesisConfig.from_dict({
        "schema": "splitchain-genesis/v1", "network_id": "test",
        "max_supply": 100, "allocations": {"alice": 100},
        "locked_accounts": [],
    })
    ledger = Ledger(genesis=genesis)
    with pytest.raises(ProtocolError, match="unknown receiver account"):
        ledger.offer("alice", "missing", 10)
    assert ledger.locked == {}
    assert ledger.branches == {}
