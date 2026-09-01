"""SplitChain service layer."""

from __future__ import annotations

from dataclasses import dataclass

from .distops import DistOPS, Workload, WorkloadManifest
from .truelies import ServiceEvent, TrueLies


@dataclass(frozen=True)
class ServiceRequest:
    request_id: str
    owner: str
    workload: Workload
    manifest: WorkloadManifest | None = None
    protocol_round: int = 0


class ServiceLayer:
    def __init__(self, distops: DistOPS, truelies: TrueLies) -> None:
        self.distops = distops
        self.truelies = truelies
        self.requests: dict[str, dict] = {}

    def execute(self, request: ServiceRequest) -> dict:
        receipt = self.distops.schedule(
            request.workload,
            request.manifest,
            current_round=request.protocol_round,
        )
        event = ServiceEvent.create(
            "distops.compute",
            request.owner,
            {"request_id": request.request_id, "receipt": receipt["result_digest"]},
        )
        proof = self.truelies.prove(event)
        if not proof["quorum"]:
            raise RuntimeError("service proof failed observer quorum")
        result = {"request_id": request.request_id, "receipt": receipt, "proof": proof}
        self.requests[request.request_id] = result
        return result
