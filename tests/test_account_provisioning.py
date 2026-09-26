import json

import pytest

from scripts.add_testnet_account import add_account


def test_new_account_keeps_private_files_and_refuses_overwrite(tmp_path):
    registry = tmp_path / "accounts.json"
    registry.write_text(json.dumps({"testnet_faucet": "f" * 64}))
    registry.chmod(0o600)
    credential = tmp_path / "alice.secret"
    add_account(registry, "alice", credential)
    accounts = json.loads(registry.read_text())
    assert credential.read_text().strip() == accounts["alice"]
    assert len(accounts["alice"]) == 64
    assert registry.stat().st_mode & 0o777 == 0o600
    assert credential.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="already exists"):
        add_account(registry, "alice", tmp_path / "again.secret")
    with pytest.raises(ValueError, match="cannot use"):
        add_account(registry, "testnet_operator", tmp_path / "operator.secret")
    with pytest.raises(FileExistsError):
        add_account(registry, "charlie", credential)
    assert "charlie" not in json.loads(registry.read_text())
