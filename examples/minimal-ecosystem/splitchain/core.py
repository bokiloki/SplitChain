import json
from dataclasses import dataclass, field
from hashlib import sha256


def digest(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Transaction:
    sender: str
    receiver: str
    amount: int
    nonce: int

    @property
    def id(self) -> str:
        return digest(self.__dict__)


@dataclass
class Branch:
    owner: str
    stake: int
    kind: str = "revocable"
    transactions: list[Transaction] = field(default_factory=list)
    active: bool = False
    finalized_rounds: int = 0

    def append(self, tx: Transaction) -> None:
        if self.transactions:
            raise ValueError("v0 permits one transaction per branch")
        if tx.sender != self.owner:
            raise ValueError("branch transaction must be signed by its owner")
        if tx.amount > self.stake:
            raise ValueError("branch value exceeds locked stake")
        self.transactions.append(tx)


@dataclass
class Node:
    node_id: str
    stake: int
    certified: bool = True
    ledger: list[str] = field(default_factory=list)

    def vote(self, candidate: str) -> str:
        if not self.certified or self.stake <= 0:
            raise PermissionError("only active certified nodes may validate")
        return candidate


class Network:
    """Deterministic in-memory reference model, not production consensus."""

    def __init__(self, nodes: list[Node], observer_quorum: int = 3):
        if len(nodes) < 3:
            raise ValueError("at least three nodes are required")
        self.nodes, self.observer_quorum, self.round = nodes, observer_quorum, 0

    def activate_branch(self, branch: Branch, acknowledgements: set[str]) -> None:
        eligible = {n.node_id for n in self.nodes if n.certified and n.stake > 0}
        if len(acknowledgements & eligible) < self.observer_quorum:
            raise ValueError("observer quorum not reached")
        branch.active = True

    def finalize(self, branch: Branch) -> str:
        if not branch.active or len(branch.transactions) != 1:
            raise ValueError("branch is not active and complete")
        candidate = branch.transactions[0].id
        votes = [n.vote(candidate) for n in self.nodes]
        if votes.count(candidate) < (2 * len(votes)) // 3 + 1:
            raise RuntimeError("Byzantine quorum not reached")
        self.round += 1
        branch.finalized_rounds += 1
        for node in self.nodes:
            node.ledger.append(candidate)
        return candidate
