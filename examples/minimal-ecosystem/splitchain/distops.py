from dataclasses import dataclass


@dataclass(frozen=True)
class Workload:
    workload_id: str
    image: str
    risk: str
    cpu: int = 1
    memory_mb: int = 256


class DistOPS:
    """Emits isolation policy; it does not launch containers."""

    def schedule(self, workload: Workload, node_reputation: int) -> dict[str, object]:
        if workload.risk not in {"low", "medium", "high"}:
            raise ValueError("risk must be low, medium, or high")
        if workload.cpu < 1 or workload.memory_mb < 64:
            raise ValueError("invalid resource request")
        if workload.risk == "high" and node_reputation < 80:
            raise PermissionError("high-risk work requires reputation >= 80")
        return {
            "runtime": "microvm" if workload.risk == "high" else "container",
            "rootless": True,
            "read_only_root": True,
            "host_network": False,
            "no_new_privileges": True,
            "cpu": workload.cpu,
            "memory_mb": workload.memory_mb,
        }
