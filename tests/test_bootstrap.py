import json
from pathlib import Path

import pytest

from splitchain import bootstrap
from splitchain.model import GenesisConfig, ProtocolError


def test_join_verifies_manifest_and_pinned_genesis(monkeypatch):
    local = Path(__file__).parents[1] / "configs/testnet-genesis.json"
    genesis = GenesisConfig.from_dict(json.loads(local.read_text()))
    base = "https://bokiloki.ddns.net/"
    expected = bootstrap.manifest(base, genesis)
    monkeypatch.setattr(bootstrap, "_fetch_json", lambda url: (
        expected if url == expected["manifest_url"] else genesis.public()
    ))
    assert bootstrap.join(base, local) == expected
    bad = {**expected, "rpc_url": "wss://attacker.invalid/rpc"}
    monkeypatch.setattr(bootstrap, "_fetch_json", lambda url: bad)
    with pytest.raises(ProtocolError, match="manifest differs"):
        bootstrap.join(base, local)
    with pytest.raises(ProtocolError, match="HTTPS origin"):
        bootstrap.join("http://bokiloki.ddns.net/", local)


def test_join_rejects_altered_published_genesis(monkeypatch):
    local = Path(__file__).parents[1] / "configs/testnet-genesis.json"
    genesis = GenesisConfig.from_dict(json.loads(local.read_text()))
    base = "https://bokiloki.ddns.net/"
    expected = bootstrap.manifest(base, genesis)
    monkeypatch.setattr(bootstrap, "_fetch_json", lambda url: (
        expected if url == expected["manifest_url"]
        else {**genesis.public(), "network_id": "other"}
    ))
    with pytest.raises(ProtocolError, match="published genesis differs"):
        bootstrap.join(base, local)
