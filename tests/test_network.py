import pytest

from splitchain.identity import CertificateAuthority
from splitchain.model import ProtocolError
from splitchain.network import GossipNode


def make_network():
    authority = CertificateAuthority("local-ca", "ca-secret")
    nodes = []
    for name in ("primary", "secondary", "tertiary"):
        certificate = authority.issue(name, f"{name}-public", ("writer",), 0, 100)
        nodes.append(GossipNode(certificate, f"{name}-secret", authority))
    for node in nodes:
        for peer in nodes:
            if peer is not node:
                node.connect(peer)
    return nodes


def test_three_node_branch_scoped_propagation():
    primary, secondary, tertiary = make_network()
    message = primary.create("branch-1", "commit", {"digest": "abc"})
    assert primary.broadcast(message, current_round=1) == 2
    assert secondary.received == [message]
    assert tertiary.received == [message]


def test_gossip_rejects_duplicate_sequence():
    primary, secondary, _ = make_network()
    message = primary.create("branch-1", "commit", {"digest": "abc"})
    primary.broadcast(message, current_round=1)
    with pytest.raises(ProtocolError, match="stale or duplicate"):
        secondary.receive(message, primary.certificate, primary._key, 1)


def test_gossip_rejects_expired_certificate():
    primary, secondary, _ = make_network()
    message = primary.create("branch-1", "commit", {"digest": "abc"})
    with pytest.raises(ProtocolError, match="certificate"):
        secondary.receive(message, primary.certificate, primary._key, 100)
