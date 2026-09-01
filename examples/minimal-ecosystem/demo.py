from hashlib import sha256

from splitchain import Branch, DistOPS, Evidence, Network, Node, Transaction, TrueLies, Workload

nodes = [Node(f"node-{i}", 100) for i in range(1, 6)]
network = Network(nodes)
branch = Branch("alice", 25)
branch.append(Transaction("alice", "bob", 20, 1))
network.activate_branch(branch, {"node-1", "node-2", "node-3"})
txid = network.finalize(branch)
payload = b"sensor-temperature=21.4"
verified = TrueLies({"node-1", "node-2", "node-3"}).verify(
    Evidence(payload, sha256(payload).hexdigest(), frozenset({"node-1", "node-2"}))
)
plan = DistOPS().schedule(Workload("job-1", "sha256:demo", "high"), 90)
print(
    {
        "nodes": len(nodes),
        "tx": txid[:12],
        "rounds": branch.finalized_rounds,
        "evidence": verified,
        "sandbox": plan,
    }
)
