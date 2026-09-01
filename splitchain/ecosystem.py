"""Application-to-node vertical slice for the SplitChain ecosystem."""

from __future__ import annotations

from .distops import ComputeNode, DistOPS, Risk, Workload
from .finality import FinalityRole, FinalitySigner, ThreeRoundFinality
from .identity import CertificateAuthority
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
        authority = CertificateAuthority("ecosystem-finality-ca", "local-demo-ca-key")
        signers = []
        for role in (
            FinalityRole.PRIMARY,
            FinalityRole.SECONDARY,
            FinalityRole.TERTIARY,
        ):
            certificate = authority.issue(
                role.value,
                f"{role.value}-public",
                ("observer",),
                0,
                10,
            )
            signers.append(
                FinalitySigner(certificate, role, f"local-{role.value}-key")
            )
        finality = ThreeRoundFinality(
            authority,
            branch.branch_id,
            branch.tx_digest or "",
            {signer.certificate.node_id: signer.role for signer in signers},
        )
        for round_number in range(1, finality.ROUNDS + 1):
            for signer in signers[:2]:
                vote = signer.vote(branch.branch_id, branch.tx_digest or "", round_number)
                finality.acknowledge(
                    vote,
                    signer.certificate,
                    signer.verification_key,
                )
        self.ledger.advance(3)
        return {
            "application": {"request": "demo-001", "status": "completed"},
            "distops": service["receipt"],
            "truelies": service["proof"],
            "finality": finality.status(),
            "protocol": self.ledger.snapshot(),
            "nodes": [node.node_id for node in self.distops.nodes],
        }
