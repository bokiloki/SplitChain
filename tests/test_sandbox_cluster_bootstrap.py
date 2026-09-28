import json

import pytest

from splitchain.model import GenesisConfig, ProtocolError
from splitchain.sandbox_cluster_bootstrap import bootstrap
from splitchain.stake_manifest import load_manifest
from splitchain.transport import PeerRegistry, TLSMaterial


def test_bootstrap_pins_six_private_sandboxes_and_authenticated_relays(tmp_path):
    genesis = tmp_path / "genesis.json"
    genesis.write_text(json.dumps({
        "schema": "splitchain-genesis/v1", "network_id": "sandbox-only",
        "max_supply": 21, "allocations": {"testnet_locked_reserve": 7, "faucet": 14},
        "locked_accounts": ["testnet_locked_reserve"],
    }))
    output = tmp_path / "deployment"
    result = bootstrap(genesis, output)
    compose = json.loads((output / "compose.json").read_text())
    assert result["nodes"] == 6
    assert len(compose["services"]) == 12
    assert compose["networks"]["isolated"]["internal"]
    parsed = GenesisConfig.from_dict(json.loads((output / "shared/genesis.json").read_text()))
    load_manifest(output / "shared/manifest.json", parsed, result["epoch_digest"])
    registry = PeerRegistry.from_path(output / "shared/registry.json")
    for name in ("primary", "secondary", "tertiary", "colleague-primary",
                 "colleague-secondary", "colleague-tertiary"):
        sandbox = compose["services"][f"sandbox-{name}"]
        relay = compose["services"][f"relay-{name}"]
        assert sandbox["network_mode"] == "none"
        assert "ports" not in relay and relay["networks"] == ["isolated"]
        tls = TLSMaterial.from_values(output / name / "tls.pem", output / name / "tls.key",
                                      output / "shared/ca.pem")
        assert tls.server_context() and tls.client_context()
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        certificate = x509.load_pem_x509_certificate((output / name / "tls.pem").read_bytes())
        assert registry.verify_der(certificate.public_bytes(serialization.Encoding.DER)).node_id == name
    with pytest.raises(ProtocolError, match="exists"):
        bootstrap(genesis, output)
