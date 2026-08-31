"""DistOPS scheduling and sandbox-policy layer."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import ClassVar


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


class DistOPS:
    """Schedules workloads while enforcing a deny-by-default sandbox contract.

    It models placement and execution receipts; it does not execute arbitrary host commands.
    """

    ALLOWED_IMAGES: ClassVar[set[str]] = {"splitchain/echo:0.1", "splitchain/hash:0.1"}

    def __init__(self, nodes: list[ComputeNode]) -> None:
        self.nodes = nodes
        self.receipts: list[dict] = []

    def schedule(self, workload: Workload) -> dict:
        if workload.image not in self.ALLOWED_IMAGES:
            raise ValueError("image is not approved by the DistOPS sandbox policy")
        candidates = [
            node
            for node in self.nodes
            if not node.busy
            and node.cpu >= workload.cpu
            and node.memory_mb >= workload.memory_mb
            and (workload.risk == Risk.LOW or node.trusted)
        ]
        if not candidates:
            raise ValueError("no eligible sandbox node")
        node = max(candidates, key=lambda item: (item.reputation, item.trusted, item.node_id))
        node.busy = True
        digest = hashlib.sha256(
            f"{node.node_id}:{workload.image}:{workload.command}".encode()
        ).hexdigest()
        receipt = {
            "node": asdict(node),
            "workload": {**asdict(workload), "risk": workload.risk.name.lower()},
            "sandbox": {
                "network": "deny",
                "filesystem": "read-only",
                "privileges": "none",
                "host_pid_namespace": False,
            },
            "result_digest": digest,
            "status": "completed",
        }
        node.busy = False
        self.receipts.append(receipt)
        return receipt
