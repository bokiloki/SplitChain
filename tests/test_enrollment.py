import asyncio
import json
from pathlib import Path

import pytest
import websockets

from splitchain.auth import RequestAuthenticator
from splitchain.enrollment import EnrollmentError, approve, request_from_file
from splitchain.model import GenesisConfig, Ledger, ProtocolError
from splitchain.node import ReferenceNode


def test_registration_is_operator_only_persisted_and_zero_supply(tmp_path):
    async def scenario():
        state = tmp_path / "ledger.json"
        secrets = {"testnet_operator": "operator-secret", "alice": "alice-secret"}
        node = ReferenceNode({"alice": 100, "bob": 0}, state_path=state, auth_secrets=secrets)
        request = {"id": 1, "method": "account.register", "params": {"account": "sc123456"}}
        request["auth"] = RequestAuthenticator.sign(request, "alice", 1, secrets["alice"])
        assert node.ledger.balances.get("sc123456") is None
        assert "error" in await node.dispatch(request)
        request["auth"] = RequestAuthenticator.sign(request, "testnet_operator", 2, secrets["testnet_operator"])
        response = await node.dispatch(request)
        assert response["result"]["height"] == 1
        assert node.ledger.balances["sc123456"] == 0
        assert sum(node.ledger.balances.values()) == 100
        assert Ledger.from_snapshot(node.ledger.snapshot()).snapshot() == node.ledger.snapshot()
        restarted = ReferenceNode({"alice": 100, "bob": 0}, state_path=state, auth_secrets=secrets)
        assert restarted.ledger.balances["sc123456"] == 0
        again = {"id": 2, "method": "account.register", "params": {"account": "sc123456"}}
        again["auth"] = RequestAuthenticator.sign(again, "testnet_operator", 3, secrets["testnet_operator"])
        assert "error" in await restarted.dispatch(again)
    asyncio.run(scenario())


def test_private_enrollment_validates_network_permissions_and_collision(tmp_path):
    genesis = GenesisConfig.from_dict(json.loads((Path(__file__).parents[1] / "configs/testnet-genesis.json").read_text()))
    request = tmp_path / "request.json"
    registry = tmp_path / "accounts.json"
    document = {"schema": "splitchain-wallet-enrollment/v1", "network_id": genesis.network_id,
                "account": "sc123456", "credential": "a" * 64}
    request.write_text(json.dumps(document))
    registry.write_text(json.dumps({"testnet_operator": "operator-secret"}))
    request.chmod(0o600)
    registry.chmod(0o600)
    assert request_from_file(request, genesis) == ("sc123456", "a" * 64)
    assert approve(request, registry, genesis) == "sc123456"
    assert approve(request, registry, genesis) == "sc123456"
    assert json.loads(registry.read_text())["sc123456"] == "a" * 64
    assert registry.stat().st_mode & 0o777 == 0o600
    document["credential"] = "b" * 64
    request.write_text(json.dumps(document))
    with pytest.raises(EnrollmentError, match="already enrolled"):
        approve(request, registry, genesis)
    document["network_id"] = "wrong-network"
    request.write_text(json.dumps(document))
    with pytest.raises(EnrollmentError, match="does not match"):
        approve(request, registry, genesis)
    request.chmod(0o644)
    with pytest.raises(EnrollmentError, match="private"):
        request_from_file(request, genesis)


def test_registration_rejects_reserved_and_invalid_accounts():
    ledger = Ledger({"alice": 100})
    for name in ("testnet_bad", "x", "UPPER", "alice"):
        with pytest.raises(ProtocolError):
            ledger.register_account(name)


def test_registration_replicates_to_follower(tmp_path):
    async def scenario():
        secrets = {"testnet_operator": "operator-secret"}
        cluster_secret = "cluster-secret-at-least-32-characters"
        secondary = ReferenceNode(state_path=tmp_path / "secondary.json", auth_secrets=secrets,
                                  node_id="secondary", role="secondary", cluster_secret=cluster_secret)
        async with websockets.serve(secondary.handler, "127.0.0.1", 0) as server:
            url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            primary = ReferenceNode(state_path=tmp_path / "primary.json", auth_secrets=secrets,
                                    node_id="primary", role="primary", cluster_secret=cluster_secret,
                                    peer_urls={"secondary": url})
            request = {"id": "new-wallet", "method": "account.register", "params": {"account": "sc987654"}}
            request["auth"] = RequestAuthenticator.sign(request, "testnet_operator", 1, secrets["testnet_operator"])
            result = await primary.dispatch(request)
            assert result["result"]["account"] == "sc987654"
            assert primary.ledger.snapshot() == secondary.ledger.snapshot()
            assert secondary.ledger.balances["sc987654"] == 0
    asyncio.run(scenario())
