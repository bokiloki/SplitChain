import hashlib
import json
import ssl

import pytest

from splitchain.model import ProtocolError
from splitchain.transport import PeerIdentity, PeerRegistry, TLSMaterial


class FakeContext:
    def __init__(self):
        self.minimum_version = None
        self.maximum_version = None
        self.verify_mode = None
        self.check_hostname = False
        self.authorities = []
        self.chains = []

    def load_verify_locations(self, *, cafile):
        self.authorities.append(cafile)

    def load_cert_chain(self, certificate, private_key):
        self.chains.append((certificate, private_key))


def material(tmp_path):
    paths = [tmp_path / name for name in ("node.crt", "node.key", "ca.crt")]
    for path in paths:
        path.write_text("test material")
    return TLSMaterial(*paths)


def test_tls_configuration_is_all_or_nothing(tmp_path):
    assert TLSMaterial.from_values(None, None, None) is None
    with pytest.raises(ProtocolError, match="requires certificate"):
        TLSMaterial.from_values(tmp_path / "node.crt", None, None)
    with pytest.raises(ProtocolError, match="does not exist"):
        TLSMaterial.from_values(*(tmp_path / name for name in ("a", "b", "c")))


def test_server_context_requires_tls13_and_client_certificate(tmp_path, monkeypatch):
    context = FakeContext()
    monkeypatch.setattr(ssl, "create_default_context", lambda *args, **kwargs: context)
    tls = material(tmp_path)
    assert tls.server_context() is context
    assert context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert context.maximum_version is ssl.TLSVersion.TLSv1_3
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.authorities == [str(tls.certificate_authority)]
    assert context.chains == [(str(tls.certificate), str(tls.private_key))]


def test_client_context_requires_tls13_hostname_and_server_certificate(tmp_path, monkeypatch):
    context = FakeContext()
    captured = {}

    def factory(*args, **kwargs):
        captured.update(kwargs)
        return context

    monkeypatch.setattr(ssl, "create_default_context", factory)
    tls = material(tmp_path)
    assert tls.client_context() is context
    assert captured["cafile"] == str(tls.certificate_authority)
    assert context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert context.maximum_version is ssl.TLSVersion.TLSv1_3
    assert context.check_hostname is True
    assert context.verify_mode is ssl.CERT_REQUIRED


def test_peer_registry_binds_certificate_to_node_and_roles(tmp_path):
    certificate = b"peer certificate in DER form"
    fingerprint = hashlib.sha256(certificate).hexdigest()
    registry_path = tmp_path / "peers.json"
    registry_path.write_text(json.dumps({"peers": [{
        "node_id": "validator-a",
        "certificate_sha256": fingerprint,
        "roles": ["secondary", "primary"],
    }]}))
    identity = PeerRegistry.from_path(registry_path).verify_der(certificate)
    assert identity.node_id == "validator-a"
    assert identity.roles == ("primary", "secondary")


def test_peer_registry_rejects_unknown_or_missing_certificates():
    registry = PeerRegistry((PeerIdentity("node-a", "a" * 64, ("client",)),))
    with pytest.raises(ProtocolError, match="not authorized"):
        registry.verify_der(b"unknown")
    with pytest.raises(ProtocolError, match="did not present"):
        registry.verify_der(None)


def test_peer_registry_rejects_duplicates_and_invalid_roles():
    with pytest.raises(ProtocolError, match="duplicate identity"):
        PeerRegistry((
            PeerIdentity("node-a", "a" * 64, ("primary",)),
            PeerIdentity("node-a", "b" * 64, ("secondary",)),
        ))
    with pytest.raises(ProtocolError, match="invalid role"):
        PeerRegistry((PeerIdentity("node-a", "a" * 64, ("host-admin",)),))
