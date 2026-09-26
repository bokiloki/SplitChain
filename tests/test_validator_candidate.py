import json
from pathlib import Path

import pytest

from splitchain.model import GenesisConfig, ProtocolError
from splitchain.validator_candidate import prepare, verify


def test_candidate_keys_prove_control_and_match_genesis(tmp_path):
    genesis = GenesisConfig.from_dict(json.loads(
        (Path(__file__).parents[1] / "configs/testnet-genesis.json").read_text()))
    output = tmp_path / "private"
    manifest = prepare(genesis, output)
    assert output.stat().st_mode & 0o777 == 0o700
    assert {node["node_id"] for node in manifest["nodes"]} == {
        "colleague-primary", "colleague-secondary", "colleague-tertiary"}
    assert (output / "public-invite.json").stat().st_mode & 0o777 == 0o600
    for node in manifest["nodes"]:
        assert (output / f"{node['node_id']}.pem").stat().st_mode & 0o777 == 0o600
    verify(manifest, genesis)
    changed = json.loads(json.dumps(manifest))
    changed["nodes"][0]["public_key"] = changed["nodes"][1]["public_key"]
    with pytest.raises(ProtocolError, match="proof"):
        verify(changed, genesis)
    changed = json.loads(json.dumps(manifest))
    changed["genesis_digest"] = "0" * 64
    with pytest.raises(ProtocolError, match="pinned genesis"):
        verify(changed, genesis)


def test_private_keys_cannot_be_generated_in_git_checkout(tmp_path):
    genesis = GenesisConfig.from_dict(json.loads(
        (Path(__file__).parents[1] / "configs/testnet-genesis.json").read_text()))
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    with pytest.raises(ProtocolError, match="outside a Git checkout"):
        prepare(genesis, checkout / "private-candidates")
    assert not (checkout / "private-candidates").exists()
