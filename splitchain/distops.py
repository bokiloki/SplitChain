"""DistOPS scheduling and sandbox-policy layer."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import ClassVar

from .model import canonical_json, protocol_digest


class Risk(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class ComputeNode:
    node_id: str
    cpu: int
    memory_mb: int
    reputation: int
    trusted: bool = False
    busy: bool = False


@dataclass(frozen=True)
class Workload:
    image: str
    command: tuple[str, ...]
    cpu: int = 1
    memory_mb: int = 128
    risk: Risk = Risk.MEDIUM
    network_allowlist: tuple[str, ...] = ()
    secret_names: tuple[str, ...] = ()

    def public(self) -> dict:
        return {**asdict(self), "risk": self.risk.name.lower()}


@dataclass(frozen=True)
class WorkloadManifest:
    issuer: str
    workload_digest: str
    valid_from: int
    valid_until: int
    signature: str

    def unsigned(self) -> dict:
        value = asdict(self)
        value.pop("signature")
        return value


class ManifestAuthority:
    """Local HMAC manifest authority for deterministic policy tests."""

    def __init__(self, issuer: str, secret: str) -> None:
        self.issuer = issuer
        self._secret = secret.encode()

    def issue(
        self, workload: Workload, valid_from: int, valid_until: int
    ) -> WorkloadManifest:
        if valid_until <= valid_from:
            raise ValueError("invalid workload manifest validity window")
        unsigned = {
            "issuer": self.issuer,
            "valid_from": valid_from,
            "valid_until": valid_until,
            "workload_digest": protocol_digest(
                "splitchain/distops-workload/v1", workload.public()
            ),
        }
        signature = hmac.new(
            self._secret, canonical_json(unsigned), hashlib.sha256
        ).hexdigest()
        return WorkloadManifest(signature=signature, **unsigned)

    def verify(
        self, workload: Workload, manifest: WorkloadManifest, current_round: int
    ) -> bool:
        expected_digest = protocol_digest(
            "splitchain/distops-workload/v1", workload.public()
        )
        expected_signature = hmac.new(
            self._secret, canonical_json(manifest.unsigned()), hashlib.sha256
        ).hexdigest()
        return (
            manifest.issuer == self.issuer
            and manifest.valid_from <= current_round < manifest.valid_until
            and manifest.workload_digest == expected_digest
            and hmac.compare_digest(expected_signature, manifest.signature)
        )


class DistOPS:
    """Schedules workloads while enforcing a deny-by-default sandbox contract.

    It models placement and execution receipts; it does not execute arbitrary host commands.
    """

    ALLOWED_IMAGES: ClassVar[set[str]] = {"splitchain/echo:0.1", "splitchain/hash:0.1"}

    REPUTATION_FLOOR: ClassVar[dict[Risk, int]] = {
        Risk.LOW: 0,
        Risk.MEDIUM: 50,
        Risk.HIGH: 80,
    }

    def __init__(
        self,
        nodes: list[ComputeNode],
        manifest_authority: ManifestAuthority | None = None,
    ) -> None:
        self.nodes = nodes
        self.manifest_authority = manifest_authority
        self.receipts: list[dict] = []

    def schedule(
        self,
        workload: Workload,
        manifest: WorkloadManifest | None = None,
        current_round: int = 0,
    ) -> dict:
        if workload.image not in self.ALLOWED_IMAGES:
            raise ValueError("image is not approved by the DistOPS sandbox policy")
        if workload.cpu < 1 or workload.memory_mb < 64:
            raise ValueError("invalid DistOPS resource quota")
        if self.manifest_authority and (
            manifest is None
            or not self.manifest_authority.verify(workload, manifest, current_round)
        ):
            raise ValueError("signed workload manifest is missing or invalid")
        candidates = [
            node
            for node in self.nodes
            if not node.busy
            and node.cpu >= workload.cpu
            and node.memory_mb >= workload.memory_mb
            and node.reputation >= self.REPUTATION_FLOOR[workload.risk]
            and (workload.risk == Risk.LOW or node.trusted)
        ]
        if not candidates:
            raise ValueError("no eligible sandbox node")
        node = max(candidates, key=lambda item: (item.reputation, item.trusted, item.node_id))
        node.busy = True
        completion_proof = protocol_digest(
            "splitchain/distops-completion/v1",
            {
                "node_id": node.node_id,
                "workload": workload.public(),
            },
        )
        receipt = {
            "node": asdict(node),
            "workload": workload.public(),
            "manifest": asdict(manifest) if manifest else None,
            "sandbox": {
                "runtime": "microvm" if workload.risk == Risk.HIGH else "container",
                "rootless": True,
                "network": (
                    {"mode": "allowlist", "destinations": workload.network_allowlist}
                    if workload.network_allowlist
                    else {"mode": "deny", "destinations": ()}
                ),
                "filesystem": "read-only",
                "privileges": "none",
                "no_new_privileges": True,
                "capabilities": "drop-all",
                "seccomp": "required",
                "host_pid_namespace": False,
                "quotas": {"cpu": workload.cpu, "memory_mb": workload.memory_mb},
                "secrets": {
                    "names": workload.secret_names,
                    "delivery": "ephemeral",
                    "persisted": False,
                    "values_exposed": False,
                },
            },
            "result_digest": completion_proof,
            "completion_proof": completion_proof,
            "status": "completed",
        }
        node.busy = False
        self.receipts.append(receipt)
        return receipt
