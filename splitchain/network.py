"""Deterministic branch-scoped signed gossip for local multi-node tests."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import asdict, dataclass

from .identity import CertificateAuthority, NodeCertificate
from .model import ProtocolError, canonical_json


@dataclass(frozen=True)
class GossipMessage:
    sender: str
    branch_id: str
    sequence: int
    kind: str
    payload: dict
    signature: str

    def unsigned(self) -> dict:
        value = asdict(self)
        value.pop("signature")
        return value


class GossipNode:
    def __init__(
        self,
        certificate: NodeCertificate,
        signing_key: str,
        authority: CertificateAuthority,
    ) -> None:
        self.certificate = certificate
        self._key = signing_key.encode()
        self.authority = authority
        self.peers: dict[str, GossipNode] = {}
        self.received: list[GossipMessage] = []
        self._incoming_sequences: dict[tuple[str, str], int] = {}
        self._outgoing_sequences: dict[str, int] = {}

    @property
    def node_id(self) -> str:
        return self.certificate.node_id

    def connect(self, peer: GossipNode) -> None:
        if peer.node_id == self.node_id:
            raise ProtocolError("node cannot peer with itself")
        self.peers[peer.node_id] = peer

    def create(self, branch_id: str, kind: str, payload: dict) -> GossipMessage:
        if not branch_id:
            raise ProtocolError("branch-scoped message requires branch id")
        sequence = self._outgoing_sequences.get(branch_id, 0) + 1
        unsigned = {
            "branch_id": branch_id,
            "kind": kind,
            "payload": payload,
            "sender": self.node_id,
            "sequence": sequence,
        }
        signature = hmac.new(self._key, canonical_json(unsigned), hashlib.sha256).hexdigest()
        self._outgoing_sequences[branch_id] = sequence
        return GossipMessage(signature=signature, **unsigned)

    def broadcast(self, message: GossipMessage, current_round: int) -> int:
        delivered = 0
        for peer in self.peers.values():
            peer.receive(message, self.certificate, self._key, current_round)
            delivered += 1
        return delivered

    def receive(
        self,
        message: GossipMessage,
        certificate: NodeCertificate,
        verification_key: bytes,
        current_round: int,
    ) -> None:
        if message.sender != certificate.node_id:
            raise ProtocolError("message sender does not match certificate")
        if not self.authority.verify(certificate, current_round, "writer"):
            raise ProtocolError("sender certificate is not valid for writer role")
        expected = hmac.new(
            verification_key, canonical_json(message.unsigned()), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, message.signature):
            raise ProtocolError("invalid gossip signature")
        scope = (message.sender, message.branch_id)
        if message.sequence <= self._incoming_sequences.get(scope, 0):
            raise ProtocolError("stale or duplicate gossip sequence")
        self._incoming_sequences[scope] = message.sequence
        self.received.append(message)
