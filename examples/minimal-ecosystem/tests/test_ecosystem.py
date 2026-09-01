import unittest
from hashlib import sha256

from splitchain import Branch, DistOPS, Evidence, Network, Node, Transaction, TrueLies, Workload


class EcosystemTests(unittest.TestCase):
    def setUp(self):
        self.network = Network([Node(f"n{i}", 100) for i in range(5)])

    def test_five_node_finality(self):
        branch = Branch("alice", 10)
        branch.append(Transaction("alice", "bob", 10, 1))
        self.network.activate_branch(branch, {"n0", "n1", "n2"})
        txid = self.network.finalize(branch)
        self.assertTrue(all(n.ledger == [txid] for n in self.network.nodes))

    def test_branch_rejects_second_transaction(self):
        branch = Branch("alice", 20)
        branch.append(Transaction("alice", "bob", 5, 1))
        with self.assertRaises(ValueError):
            branch.append(Transaction("alice", "carol", 5, 2))

    def test_observer_quorum(self):
        with self.assertRaises(ValueError):
            self.network.activate_branch(Branch("alice", 10), {"n0", "n1"})

    def test_truelies_rejects_tampering(self):
        evidence = Evidence(b"changed", sha256(b"original").hexdigest(), frozenset({"n0", "n1"}))
        self.assertFalse(TrueLies({"n0", "n1"}).verify(evidence))

    def test_distops_high_risk_gate(self):
        with self.assertRaises(PermissionError):
            DistOPS().schedule(Workload("w", "sha256:x", "high"), 79)


if __name__ == "__main__":
    unittest.main()
