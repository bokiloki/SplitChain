import asyncio
import datetime
import hashlib
import json
import stat
import time

import pytest
import websockets
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from splitchain.failover import FailoverAuthority, LeadershipState
from splitchain.model import ProtocolError
from splitchain.node import ReferenceNode
from splitchain.node_identity import NodeKeyring, generate_node_key
from splitchain.replication import ReplicationAuthenticator
from splitchain.transport import PeerIdentity, PeerRegistry, TLSMaterial


def keyrings(tmp_path):
    registry = {}
    for role in ("primary", "secondary", "tertiary"):
        registry[role] = generate_node_key(role, tmp_path / f"{role}.pem")
    registry_path = tmp_path / "peers.json"
    registry_path.write_text(json.dumps(registry))
    return {
        role: NodeKeyring.from_files(role, tmp_path / f"{role}.pem", registry_path)
        for role in registry
    }


def test_node_keys_are_distinct_private_and_do_not_overwrite(tmp_path):
    keys = keyrings(tmp_path)
    assert set(keys) == {"primary", "secondary", "tertiary"}
    assert stat.S_IMODE((tmp_path / "primary.pem").stat().st_mode) == 0o600
    with pytest.raises(ProtocolError, match="cannot create"):
        generate_node_key("primary", tmp_path / "primary.pem")
    with pytest.raises(ProtocolError, match="cannot sign for another"):
        keys["secondary"].sign("primary", b"impersonation")


def test_keyring_rejects_mismatched_private_key_and_public_registry(tmp_path):
    keyrings(tmp_path)
    with pytest.raises(ProtocolError, match="does not match"):
        NodeKeyring.from_files("primary", tmp_path / "secondary.pem", tmp_path / "peers.json")


def test_ed25519_replication_and_quorum_certificate_survive_restore(tmp_path):
    keys = keyrings(tmp_path)
    signer = ReplicationAuthenticator(keyring=keys["primary"])
    verifier = ReplicationAuthenticator(keyring=keys["secondary"])
    envelope = signer.sign("primary", 1, {"method": "advance", "params": {"rounds": 1}})
    assert verifier.verify(envelope, 0)[1] == 1
    envelope["mutation"]["params"]["rounds"] = 2
    with pytest.raises(ProtocolError, match="replayed"):
        verifier.verify(envelope, 0)

    secondary = FailoverAuthority(keyring=keys["secondary"])
    tertiary = FailoverAuthority(keyring=keys["tertiary"])
    observer = FailoverAuthority(keyring=keys["primary"])
    state = LeadershipState(observer)
    first = secondary.vote("secondary", 1, "primary", "secondary", 4, 0)
    second = tertiary.vote("tertiary", 1, "primary", "secondary", 4, 0)
    assert state.submit(first) is None
    assert state.submit(second).leader == "secondary"
    assert LeadershipState.from_snapshot(observer, state.snapshot()).snapshot() == state.snapshot()
    with pytest.raises(ProtocolError, match="cannot sign for another"):
        secondary.vote("tertiary", 2, "secondary", "tertiary", 8, 0)


def test_distinct_node_keys_replicate_and_certify_takeover(tmp_path):
    async def scenario():
        keys = keyrings(tmp_path)
        secondary = ReferenceNode(
            node_id="secondary", role="secondary", keyring=keys["secondary"]
        )
        tertiary = ReferenceNode(
            node_id="tertiary", role="tertiary", keyring=keys["tertiary"]
        )
        async with (
            websockets.serve(secondary.handler, "127.0.0.1", 0) as second_server,
            websockets.serve(tertiary.handler, "127.0.0.1", 0) as third_server,
        ):
            second_url = f"ws://127.0.0.1:{second_server.sockets[0].getsockname()[1]}"
            third_url = f"ws://127.0.0.1:{third_server.sockets[0].getsockname()[1]}"
            primary = ReferenceNode(
                node_id="primary", role="primary", keyring=keys["primary"],
                peer_urls={"secondary": second_url, "tertiary": third_url},
            )
            secondary.peer_urls = {"tertiary": third_url}
            assert "result" in await primary.dispatch({
                "id": 1, "method": "advance", "params": {"rounds": 1},
            })
            assert primary.ledger.snapshot() == secondary.ledger.snapshot()
            tick = int(time.time() // 2)
            await primary.failover_step(tick - 4)
            await secondary.failover_step(tick)
            assert secondary.leadership.leader == tertiary.leadership.leader == "secondary"
            assert "result" in await secondary.dispatch({
                "id": 2, "method": "advance", "params": {"rounds": 1},
            })
            assert secondary.ledger.snapshot() == tertiary.ledger.snapshot()

    asyncio.run(scenario())


def test_signed_replication_uses_mutual_tls_and_pinned_peer_role(tmp_path):
    def certificate(name, key, issuer, issuer_key, *, ca=False):
        now = datetime.datetime.now(datetime.UTC)
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        )
        if not ca:
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False
            )
        return builder.sign(issuer_key, hashes.SHA256())

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SplitChain test CA")])
    ca = certificate("SplitChain test CA", ca_key, ca_name, ca_key, ca=True)
    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    materials = {}
    fingerprints = {}
    for role in ("primary", "secondary"):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = certificate(role, key, ca.subject, ca_key)
        cert_path, key_path = tmp_path / f"{role}.crt", tmp_path / f"{role}.tls-key"
        cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        key_path.write_bytes(key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
        materials[role] = TLSMaterial(cert_path, key_path, ca_path)
        fingerprints[role] = hashlib.sha256(
            cert.public_bytes(serialization.Encoding.DER)
        ).hexdigest()

    async def scenario():
        keys = keyrings(tmp_path)
        allowed = PeerRegistry((
            PeerIdentity("primary", fingerprints["primary"], ("primary",)),
        ))
        secondary = ReferenceNode(
            node_id="secondary", role="secondary", keyring=keys["secondary"],
            peer_registry=allowed, tls=materials["secondary"],
        )
        async with websockets.serve(
            secondary.handler, "localhost", 0, ssl=materials["secondary"].server_context()
        ) as server:
            port = server.sockets[0].getsockname()[1]
            primary = ReferenceNode(
                node_id="primary", role="primary", keyring=keys["primary"],
                tls=materials["primary"],
                peer_urls={"secondary": f"wss://localhost:{port}"},
            )
            assert "result" in await primary.dispatch({
                "id": 1, "method": "advance", "params": {"rounds": 1},
            })
            assert primary.ledger.snapshot() == secondary.ledger.snapshot()
            wrong_identity = PeerIdentity("tertiary", "0" * 64, ("client",))
            refused = await secondary.dispatch({
                "id": 2, "method": "replica.position", "params": {},
            }, wrong_identity)
            assert "not authorized" in refused["error"]["message"]
            impersonation = await secondary.dispatch({
                "id": 3, "method": "cluster.heartbeat",
                "params": secondary.leadership.authority.sign_heartbeat(
                    "secondary", 0, 4, 0
                ),
            }, PeerIdentity("primary", fingerprints["primary"], ("primary",)))
            assert "does not match TLS peer" in impersonation["error"]["message"]

    asyncio.run(scenario())
