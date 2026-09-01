import pytest

from splitchain.model import ProtocolError, protocol_digest
from splitchain.runtime import (
    ContainerRuntime,
    EphemeralSecretProvider,
    RuntimeAuthority,
    TrustedEgressGateway,
)


def receipt(image=None):
    workload = {
        "image": image or f"splitchain/hash@sha256:{'a' * 64}",
        "command": ("hash", "demo"),
        "cpu": 1,
        "memory_mb": 128,
        "risk": "medium",
        "network_allowlist": (),
        "secret_names": (),
    }
    value = {
        "node": {"node_id": "trusted-a"},
        "workload": workload,
        "sandbox": {
            "rootless": True,
            "runtime": "container",
            "network": {"mode": "deny", "destinations": ()},
            "filesystem": "read-only",
            "privileges": "none",
            "no_new_privileges": True,
            "capabilities": "drop-all",
            "seccomp": "required",
            "host_pid_namespace": False,
            "quotas": {"cpu": 1, "memory_mb": 128},
            "secrets": {"names": (), "delivery": "ephemeral", "persisted": False},
        },
    }
    value["completion_proof"] = protocol_digest(
        "splitchain/distops-completion/v1",
        {"node_id": "trusted-a", "workload": workload},
    )
    return value


def authority_and_attestation():
    authority = RuntimeAuthority("runtime-ca", "attestation-secret")
    return authority, authority.issue("docker", "27.1", 0, 10, "nonce-1")


def test_runtime_builds_argv_without_shell_and_returns_proof():
    authority, attestation = authority_and_attestation()
    captured = {}

    def runner(command):
        captured["command"] = command
        return 0, b"result", b""

    result = ContainerRuntime(
        authority, runner, enable_execution=True
    ).execute(receipt(), attestation, 1)
    assert captured["command"][0:2] == ("docker", "run")
    assert "--cap-drop" in captured["command"]
    assert result.exit_code == 0
    assert len(result.runtime_proof) == 64


def test_runtime_is_disabled_by_default():
    authority, attestation = authority_and_attestation()
    with pytest.raises(ProtocolError, match="execution is disabled"):
        ContainerRuntime(authority).execute(receipt(), attestation, 1)


def test_runtime_requires_digest_pinned_image():
    authority, attestation = authority_and_attestation()
    with pytest.raises(ProtocolError, match="immutable"):
        ContainerRuntime(
            authority, lambda command: (0, b"", b""), enable_execution=True
        ).execute(receipt("splitchain/hash:latest"), attestation, 1)


def test_runtime_rejects_privilege_or_network_policy_weakening():
    authority, attestation = authority_and_attestation()
    privileged = receipt()
    privileged["sandbox"]["privileges"] = "host"
    with pytest.raises(ProtocolError, match="isolation contract"):
        ContainerRuntime(
            authority, lambda command: (0, b"", b""), enable_execution=True
        ).execute(privileged, attestation, 1)
    networked = receipt()
    networked["sandbox"]["network"] = {
        "mode": "allowlist",
        "destinations": ("example.com:443",),
    }
    with pytest.raises(ProtocolError, match="trusted egress gateway"):
        ContainerRuntime(
            authority, lambda command: (0, b"", b""), enable_execution=True
        ).execute(networked, attestation, 1)


def test_runtime_rejects_tampered_completion_proof_and_attestation():
    authority, attestation = authority_and_attestation()
    runtime = ContainerRuntime(
        authority, lambda command: (0, b"", b""), enable_execution=True
    )
    tampered = receipt()
    tampered["completion_proof"] = "0" * 64
    with pytest.raises(ProtocolError, match="completion proof"):
        runtime.execute(tampered, attestation, 1)
    with pytest.raises(ProtocolError, match="attestation"):
        runtime.execute(receipt(), attestation, 10)


def test_runtime_attestation_nonce_cannot_be_replayed():
    authority, attestation = authority_and_attestation()
    runtime = ContainerRuntime(
        authority, lambda command: (0, b"", b""), enable_execution=True
    )
    runtime.execute(receipt(), attestation, 1)
    with pytest.raises(ProtocolError, match="nonce was already used"):
        runtime.execute(receipt(), attestation, 1)


def test_trusted_egress_gateway_enforces_allowlist_and_blocks_consensus():
    authority, attestation = authority_and_attestation()
    runtime = ContainerRuntime(
        authority, lambda command: (0, b"", b""), enable_execution=True
    )
    gateway = TrustedEgressGateway("gateway-a", "distops-egress", 90)
    networked = receipt()
    networked["sandbox"]["network"] = {
        "mode": "allowlist",
        "destinations": ("storage.internal:443",),
    }
    result = runtime.execute(networked, attestation, 1, gateway)
    assert result.command[result.command.index("--network") + 1] == "distops-egress"
    blocked = receipt()
    blocked["sandbox"]["network"] = {
        "mode": "allowlist",
        "destinations": ("validator.internal:8765",),
    }
    fresh = authority.issue("docker", "27.1", 0, 10, "nonce-2")
    with pytest.raises(ProtocolError, match="consensus endpoints"):
        runtime.execute(blocked, fresh, 1, gateway)


def test_ephemeral_secret_lease_is_one_time_and_expires():
    provider = EphemeralSecretProvider({"job-token": b"sensitive"})
    lease = provider.issue(("job-token",), current_round=3, ttl_rounds=2)
    assert "sensitive" not in repr(lease.public())
    assert lease.consume(4) == {"job-token": b"sensitive"}
    assert lease.public()["consumed"] is True
    with pytest.raises(ProtocolError, match="already consumed"):
        lease.consume(4)
    expired = provider.issue(("job-token",), current_round=3, ttl_rounds=1)
    with pytest.raises(ProtocolError, match="expired"):
        expired.consume(4)
