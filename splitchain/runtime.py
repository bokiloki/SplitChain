"""Opt-in container runtime adapter for DistOPS sandbox receipts."""

from __future__ import annotations

import hashlib
import hmac
import re
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass

from .model import ProtocolError, canonical_json, protocol_digest


@dataclass(frozen=True)
class RuntimeAttestation:
    issuer: str
    engine: str
    version: str
    nonce: str
    rootless: bool
    seccomp: bool
    no_new_privileges: bool
    valid_from: int
    valid_until: int
    signature: str

    def unsigned(self) -> dict:
        value = asdict(self)
        value.pop("signature")
        return value


class RuntimeAuthority:
    """Local HMAC attestation authority for deterministic runtime tests."""

    def __init__(self, issuer: str, secret: str) -> None:
        self.issuer = issuer
        self._secret = secret.encode()

    def issue(
        self,
        engine: str,
        version: str,
        valid_from: int,
        valid_until: int,
        nonce: str,
        *,
        rootless: bool = True,
        seccomp: bool = True,
        no_new_privileges: bool = True,
    ) -> RuntimeAttestation:
        if valid_until <= valid_from or not nonce:
            raise ProtocolError("invalid runtime attestation window")
        unsigned = {
            "engine": engine,
            "issuer": self.issuer,
            "no_new_privileges": no_new_privileges,
            "nonce": nonce,
            "rootless": rootless,
            "seccomp": seccomp,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "version": version,
        }
        signature = hmac.new(
            self._secret, canonical_json(unsigned), hashlib.sha256
        ).hexdigest()
        return RuntimeAttestation(signature=signature, **unsigned)

    def verify(self, attestation: RuntimeAttestation, current_round: int) -> bool:
        expected = hmac.new(
            self._secret,
            canonical_json(attestation.unsigned()),
            hashlib.sha256,
        ).hexdigest()
        return (
            attestation.issuer == self.issuer
            and attestation.valid_from <= current_round < attestation.valid_until
            and attestation.engine in {"docker", "containerd"}
            and attestation.rootless
            and attestation.seccomp
            and attestation.no_new_privileges
            and hmac.compare_digest(expected, attestation.signature)
        )


class ContainerCommandBuilder:
    DIGEST_IMAGE = re.compile(r"^[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")

    def build(
        self,
        receipt: dict,
        egress_gateway: TrustedEgressGateway | None = None,
    ) -> tuple[str, ...]:
        sandbox = receipt.get("sandbox", {})
        workload = receipt.get("workload", {})
        image = workload.get("image", "")
        command = workload.get("command", ())
        if not self.DIGEST_IMAGE.fullmatch(image):
            raise ProtocolError("runtime requires an immutable sha256 image reference")
        if not isinstance(command, (tuple, list)) or not all(
            isinstance(part, str) and part for part in command
        ):
            raise ProtocolError("runtime command must be a non-empty argument vector")
        required = {
            "rootless": True,
            "filesystem": "read-only",
            "privileges": "none",
            "no_new_privileges": True,
            "capabilities": "drop-all",
            "seccomp": "required",
            "host_pid_namespace": False,
        }
        if any(sandbox.get(key) != value for key, value in required.items()):
            raise ProtocolError("receipt does not satisfy the runtime isolation contract")
        network = sandbox.get("network", {})
        if network.get("mode") == "deny":
            network_name = "none"
        elif network.get("mode") == "allowlist" and egress_gateway:
            egress_gateway.authorize(tuple(network.get("destinations", ())))
            network_name = egress_gateway.network_name
        else:
            raise ProtocolError("allowlist networking requires a trusted egress gateway")
        quotas = sandbox.get("quotas", {})
        cpu = int(quotas.get("cpu", 0))
        memory_mb = int(quotas.get("memory_mb", 0))
        if cpu < 1 or memory_mb < 64:
            raise ProtocolError("runtime receipt has invalid quotas")
        if sandbox.get("secrets", {}).get("names"):
            raise ProtocolError("secret delivery requires an external ephemeral-secret provider")
        return (
            "docker",
            "run",
            "--rm",
            "--read-only",
            "--network",
            network_name,
            "--pids-limit",
            "256",
            "--cpus",
            str(cpu),
            "--memory",
            f"{memory_mb}m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--security-opt",
            "seccomp=default",
            image,
            *command,
        )


@dataclass(frozen=True)
class ExecutionResult:
    command: tuple[str, ...]
    exit_code: int
    stdout_digest: str
    stderr_digest: str
    runtime_proof: str


Runner = Callable[[tuple[str, ...]], tuple[int, bytes, bytes]]


@dataclass(frozen=True)
class TrustedEgressGateway:
    gateway_id: str
    network_name: str
    reputation: int
    trusted: bool = True

    CONSENSUS_NAMES = (
        "consensus",
        "validator",
        "finality",
        "overlord",
        "splitd",
    )

    def authorize(self, destinations: tuple[str, ...]) -> None:
        if not self.trusted or self.reputation < 80:
            raise ProtocolError("egress gateway is not trusted and high-reputation")
        if not destinations:
            raise ProtocolError("egress allowlist is empty")
        for destination in destinations:
            host, separator, port = destination.rpartition(":")
            if not separator or not host or not port.isdigit():
                raise ProtocolError("invalid egress destination")
            lowered = host.lower()
            if any(name in lowered for name in self.CONSENSUS_NAMES):
                raise ProtocolError("sandbox workloads cannot access consensus endpoints")


@dataclass
class SecretLease:
    lease_id: str
    expires_round: int
    _values: dict[str, bytearray]
    consumed: bool = False

    def consume(self, current_round: int) -> dict[str, bytes]:
        if self.consumed:
            raise ProtocolError("secret lease was already consumed")
        if current_round >= self.expires_round:
            raise ProtocolError("secret lease expired")
        result = {name: bytes(value) for name, value in self._values.items()}
        for value in self._values.values():
            value[:] = b"\x00" * len(value)
        self._values.clear()
        self.consumed = True
        return result

    def public(self) -> dict:
        return {
            "lease_id": self.lease_id,
            "expires_round": self.expires_round,
            "secret_names": tuple(sorted(self._values)),
            "consumed": self.consumed,
        }


class EphemeralSecretProvider:
    def __init__(self, secrets: dict[str, bytes]) -> None:
        self._secrets = {name: bytes(value) for name, value in secrets.items()}
        self._counter = 0

    def issue(
        self,
        names: tuple[str, ...],
        current_round: int,
        ttl_rounds: int = 1,
    ) -> SecretLease:
        if ttl_rounds < 1 or not names:
            raise ProtocolError("invalid ephemeral secret lease request")
        try:
            values = {name: bytearray(self._secrets[name]) for name in names}
        except KeyError as exc:
            raise ProtocolError("unknown ephemeral secret") from exc
        self._counter += 1
        lease_id = protocol_digest(
            "splitchain/distops-secret-lease/v1",
            {
                "counter": self._counter,
                "expires_round": current_round + ttl_rounds,
                "names": names,
            },
        )[:20]
        return SecretLease(lease_id, current_round + ttl_rounds, values)


class ContainerRuntime:
    def __init__(
        self,
        authority: RuntimeAuthority,
        runner: Runner | None = None,
        *,
        enable_execution: bool = False,
    ) -> None:
        self.authority = authority
        self.builder = ContainerCommandBuilder()
        self.runner = runner
        self.enable_execution = enable_execution
        self._attestation_nonces: set[str] = set()

    def execute(
        self,
        receipt: dict,
        attestation: RuntimeAttestation,
        current_round: int,
        egress_gateway: TrustedEgressGateway | None = None,
    ) -> ExecutionResult:
        if not self.authority.verify(attestation, current_round):
            raise ProtocolError("runtime attestation is invalid")
        if attestation.nonce in self._attestation_nonces:
            raise ProtocolError("runtime attestation nonce was already used")
        command = self.builder.build(receipt, egress_gateway)
        expected_completion = protocol_digest(
            "splitchain/distops-completion/v1",
            {
                "node_id": receipt["node"]["node_id"],
                "workload": receipt["workload"],
            },
        )
        if receipt.get("completion_proof") != expected_completion:
            raise ProtocolError("DistOPS completion proof does not match the receipt")
        if not self.enable_execution or self.runner is None:
            raise ProtocolError("container execution is disabled")
        self._attestation_nonces.add(attestation.nonce)
        exit_code, stdout, stderr = self.runner(command)
        runtime_proof = protocol_digest(
            "splitchain/distops-runtime-result/v1",
            {
                "attestation": attestation.unsigned(),
                "command": command,
                "completion_proof": expected_completion,
                "exit_code": exit_code,
                "stderr_digest": hashlib.sha256(stderr).hexdigest(),
                "stdout_digest": hashlib.sha256(stdout).hexdigest(),
            },
        )
        return ExecutionResult(
            command,
            exit_code,
            hashlib.sha256(stdout).hexdigest(),
            hashlib.sha256(stderr).hexdigest(),
            runtime_proof,
        )


def subprocess_runner(
    command: tuple[str, ...],
    *,
    timeout: int = 60,
) -> tuple[int, bytes, bytes]:
    """Execute an already validated argv without a shell. Explicit opt-in only."""

    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        shell=False,
        timeout=timeout,
    )
    return completed.returncode, completed.stdout, completed.stderr
