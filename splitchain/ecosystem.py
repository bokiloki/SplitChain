"""Application-to-node vertical slice for the SplitChain ecosystem."""

from __future__ import annotations

from .distops import ComputeNode, DistOPS, Risk, Workload
from .model import Ledger
from .services import ServiceLayer, ServiceRequest
from .truelies import TrueLies


class Ecosystem:
    def __init__(self) -> None:
        self.ledger = Ledger({"alice": 1_000, "bob": 1_000})
        self.truelies = TrueLies()
        self.distops = DistOPS(
            [
                ComputeNode("trusted-a", 8, 8_192, 100, trusted=True),
                ComputeNode("edge-b", 4, 4_096, 35, trusted=False),
                ComputeNode("trusted-c", 16, 16_384, 80, trusted=True),
            ]
        )
        self.services = ServiceLayer(self.distops, self.truelies)

    def demo(self) -> dict:
        """Run one request through every implemented ecosystem layer."""
        service = self.services.execute(
            ServiceRequest(
                "demo-001",
                "alice",
                Workload("splitchain/hash:0.1", ("hash", "demo"), risk=Risk.MEDIUM),
            )
        )
        branch = self.ledger.offer("alice", "bob", 10)
        self.ledger.accept(branch.branch_id, "bob")
        self.ledger.commit(
            branch.branch_id,
            "alice",
            {"service_proof": service["proof"]["event"]["event_id"]},
        )
        self.ledger.advance(3)
        return {
            "application": {"request": "demo-001", "status": "completed"},
            "distops": service["receipt"],
            "truelies": service["proof"],
            "protocol": self.ledger.snapshot(),
            "nodes": [node.node_id for node in self.distops.nodes],
        }

