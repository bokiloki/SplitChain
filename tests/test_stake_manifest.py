import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from splitchain.membership import StakeMembership
from splitchain.model import GenesisConfig, ProtocolError
from splitchain.stake_manifest import load_manifest
from splitchain.stake_votes import StakeVoteBook


def test_sandbox_manifest_pins_supply_keys_and_epoch(tmp_path):
    genesis = GenesisConfig.from_dict({
        "schema": "splitchain-genesis/v1", "network_id": "sandbox-test", "max_supply": 21,
        "allocations": {"faucet": 14, "reserve": 7}, "locked_accounts": ["reserve"],
    })
    private = {name: Ed25519PrivateKey.generate() for name in ("a", "b", "candidate")}
    public = {name: key.public_key() for name, key in private.items()}
    membership = StakeMembership(0, (("a", 4), ("b", 3), ("candidate", 0)), 7)
    expected = StakeVoteBook(membership, genesis.network_id, genesis.digest(), public)
    manifest = {
        "schema": "splitchain/stake-manifest/v1", "network_id": genesis.network_id,
        "genesis_digest": genesis.digest(), "reserve_account": "reserve", "epoch": 0,
        "allocations": dict(membership.allocations),
        "public_keys": {name: base64.b64encode(key.public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw,
        )).decode() for name, key in public.items()},
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    recovered, keys = load_manifest(path, genesis, expected.epoch_digest)
    assert recovered == membership
    assert StakeVoteBook(recovered, genesis.network_id,
                         genesis.digest(), keys).epoch_digest == expected.epoch_digest
    manifest["public_keys"]["a"] = manifest["public_keys"]["b"]
    path.write_text(json.dumps(manifest))
    with pytest.raises(ProtocolError, match="manifest"):
        load_manifest(path, genesis, expected.epoch_digest)
    manifest["allocations"]["a"] = 8
    path.write_text(json.dumps(manifest))
    with pytest.raises(ProtocolError, match="manifest"):
        load_manifest(path, genesis, expected.epoch_digest)
