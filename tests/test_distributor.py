"""Exercise actual model transitions and restart reconciliation for batch funding."""

import asyncio
import json
import stat
from pathlib import Path

import pytest

from splitchain.auth import RequestAuthenticator
from splitchain.distributor import (
    DistributionError,
    Distributor,
    credential,
    plan_for,
    prepare,
)
from splitchain.model import GenesisConfig, Ledger


def genesis():
    path = Path(__file__).parents[1] / "configs/testnet-genesis.json"
    return GenesisConfig.from_dict(json.loads(path.read_text(encoding="utf-8")))


def setup_batch(tmp_path, count=3):
    registry = tmp_path / "accounts.json"
    registry.write_text(json.dumps({"testnet_faucet": "f" * 64}), encoding="utf-8")
    registry.chmod(0o600)
    output = tmp_path / "sms"
    plan = plan_for("friend", count, 100, genesis())
    prepare(registry, output, plan)
    return registry, output, plan


def transport(distributor, ledger, authenticator, *, lose_offer=False):
    lost = [False]

    async def status():
        return ledger.snapshot()

    async def call(request):
        actor = authenticator.verify(request)
        params = request["params"]
        method = request["method"]
        assert actor == params.get({"offer": "sender", "accept": "receiver", "commit": "sender"}[method])
        branch = getattr(ledger, method)(**params)
        if lose_offer and method == "offer" and not lost[0]:
            lost[0] = True
            raise DistributionError("response lost after successful offer")
        return {"id": request["id"], "result": branch.public()}

    distributor.snapshot = status
    distributor.call = call


def test_prepare_private_sms_and_idempotent_registry(tmp_path, capsys):
    registry, output, plan = setup_batch(tmp_path)
    registry_before = registry.read_bytes()
    prepare(registry, output, plan)
    assert registry.read_bytes() == registry_before
    assert plan["total"] == 300
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    for actor in plan["recipients"]:
        path = output / f"{actor}.sms.txt"
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert credential(path, actor) == json.loads(registry_before)[actor]
        assert "https://bokiloki.ddns.net/splitchain/downloads" in path.read_text()
        assert credential(path, actor) not in capsys.readouterr().out


def test_complete_funding_and_restart_never_double_pays(tmp_path):
    registry, output, plan = setup_batch(tmp_path)
    accounts = json.loads(registry.read_text())
    ledger = Ledger(genesis=genesis())
    auth = RequestAuthenticator(accounts)
    distributor = Distributor(registry, output, genesis(), rpc_interval=0)
    transport(distributor, ledger, auth)
    asyncio.run(distributor.run(wait_finality=False))
    assert next(iter(ledger.branches.values())).state.value == "committed"
    assert len(ledger.branches) == 3
    ledger.advance(3)
    restarted = Distributor(registry, output, genesis(), rpc_interval=0)
    transport(restarted, ledger, auth)
    asyncio.run(restarted.run(wait_finality=True))
    assert all(ledger.balances[a] == 100 for a in plan["recipients"])
    assert ledger.balances["testnet_faucet"] == 14_000_000 - 300
    assert len(ledger.branches) == 3


def test_lost_offer_response_reconciles_without_duplicate(tmp_path):
    registry, output, _ = setup_batch(tmp_path, count=1)
    accounts = json.loads(registry.read_text())
    ledger = Ledger(genesis=genesis())
    auth = RequestAuthenticator(accounts)
    initial = Distributor(registry, output, genesis(), rpc_interval=0)
    transport(initial, ledger, auth, lose_offer=True)
    with pytest.raises(DistributionError, match="response lost"):
        asyncio.run(initial.run(wait_finality=False))
    assert len(ledger.branches) == 1
    recovered = Distributor(registry, output, genesis(), rpc_interval=0)
    transport(recovered, ledger, auth)
    asyncio.run(recovered.run(wait_finality=False))
    assert len(ledger.branches) == 1
    assert next(iter(ledger.branches.values())).state.value == "committed"


def test_uncertain_absent_offer_stops_instead_of_double_sending(tmp_path):
    registry, output, _ = setup_batch(tmp_path, count=1)
    ledger = Ledger(genesis=genesis())
    initial = Distributor(registry, output, genesis(), rpc_interval=0)

    async def silent_network(request):
        raise DistributionError("timeout")

    async def status():
        return ledger.snapshot()

    initial.call = silent_network
    initial.snapshot = status
    with pytest.raises(DistributionError, match="timeout"):
        asyncio.run(initial.run(wait_finality=False))
    recovered = Distributor(registry, output, genesis(), rpc_interval=0)
    recovered.snapshot = status
    with pytest.raises(DistributionError, match="uncertain offer"):
        asyncio.run(recovered.run(wait_finality=False))
    assert not ledger.branches


def test_full_100_friend_batch_totals_10000_units(tmp_path):
    registry, output, plan = setup_batch(tmp_path, count=100)
    assert plan["total"] == 10_000
    ledger = Ledger(genesis=genesis())
    auth = RequestAuthenticator(json.loads(registry.read_text()))
    distributor = Distributor(registry, output, genesis(), rpc_interval=0)
    transport(distributor, ledger, auth)
    asyncio.run(distributor.run(wait_finality=False))
    ledger.advance(3)
    assert len(ledger.branches) == 100
    assert all(ledger.balances[a] == 100 for a in plan["recipients"])
    assert ledger.balances["testnet_faucet"] == 14_000_000 - 10_000
