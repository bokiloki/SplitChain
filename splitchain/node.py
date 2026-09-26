"""Minimal JSON-over-WebSocket reference node for local experiments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from .auth import RequestAuthenticator
from .ecosystem import Ecosystem
from .failover import (
    ROLE_ORDER,
    FailoverAuthority,
    FailureVote,
    LeadershipCertificate,
    LeadershipState,
)
from .model import Ledger, ProtocolError, canonical_json
from .node_identity import NodeKeyring
from .persistence import LedgerStore
from .replication import ReplicationAuthenticator
from .transport import PeerIdentity, PeerRegistry, TLSMaterial


class CommitUncertain(ProtocolError):
    """The leader committed, but no replica confirmed the decision to the client."""


def parse_peer_values(values: list[str]) -> dict[str, str]:
    peers: dict[str, str] = {}
    for value in values:
        node_id, separator, url = value.partition("=")
        parsed = urlparse(url)
        if (
            not separator
            or not re.fullmatch(r"[a-zA-Z0-9._-]{1,64}", node_id)
            or parsed.scheme not in {"ws", "wss"}
            or not parsed.hostname
            or parsed.path not in {"", "/"}
            or parsed.params
            or parsed.query
            or parsed.fragment
            or node_id in peers
        ):
            raise ProtocolError("peer must be a unique NODE_ID=ws[s]://HOST:PORT value")
        peers[node_id] = url
    return peers


class ReferenceNode:
    MUTATING_METHODS: ClassVar[set[str]] = {"offer", "accept", "commit", "cancel", "advance"}

    def __init__(
        self,
        balances: dict[str, int] | None = None,
        state_path: str | Path | None = None,
        auth_secrets: dict[str, str] | None = None,
        peer_registry: PeerRegistry | None = None,
        node_id: str = "local",
        peer_urls: dict[str, str] | None = None,
        role: str = "standalone",
        cluster_secret: str | None = None,
        keyring: NodeKeyring | None = None,
        tls: TLSMaterial | None = None,
    ) -> None:
        initial = balances or {"alice": 1_000, "bob": 1_000}
        self.store = LedgerStore(state_path) if state_path else None
        self.authenticator = RequestAuthenticator(auth_secrets) if auth_secrets else None
        self.peer_registry = peer_registry
        self.node_id = node_id
        self.peer_urls = dict(peer_urls or {})
        self.role = role
        if keyring and cluster_secret:
            raise ProtocolError("cannot combine node keys with a shared cluster secret")
        if keyring and (node_id != role or keyring.role != role):
            raise ProtocolError("node identity must match its configured role")
        self.replication = (
            ReplicationAuthenticator(cluster_secret, keyring=keyring)
            if cluster_secret or keyring else None
        )
        self._peer_ssl = tls.client_context() if tls else None
        self.replication_nonces: dict[str, int] = {}
        self.replication_log: list[dict] = []
        self.replication_pending: dict | None = None
        if self.store:
            self.ledger, replay_nonces, self.replication_nonces = self.store.load_full_node_state(
                initial
            )
            if self.authenticator:
                self.authenticator.restore(replay_nonces)
            self.replication_log = self.store.load_replication_log()
            self.replication_pending = self.store.load_replication_pending()
        else:
            self.ledger = Ledger(initial)
        self.leadership: LeadershipState | None = None
        if self.replication and self.role in ROLE_ORDER:
            # Research-only derived keys: replace the shared secret with independent node keys.
            if keyring:
                authority = FailoverAuthority(keyring=keyring)
            else:
                keys = {
                    role: hashlib.sha256(
                        f"splitchain/failover/v1:{role}:{cluster_secret}".encode()
                    ).hexdigest()
                    for role in ROLE_ORDER
                }
                authority = FailoverAuthority(keys)
            saved = self.store.load_leadership_snapshot() if self.store else None
            self.leadership = (
                LeadershipState.from_snapshot(authority, saved)
                if saved is not None else LeadershipState(authority)
            )
            committed = self._committed_position()
            if saved is None:
                self.leadership.committed_nonce = committed
            elif self.leadership.committed_nonce != committed:
                raise ProtocolError("leadership record does not match committed replica position")
        if self.replication and self.replication_log:
            last_nonce = self._committed_position() - len(self.replication_log)
            if last_nonce < 0:
                raise ProtocolError("replication log exceeds committed position")
            for envelope in self.replication_log:
                expected = "primary"
                for certificate in self.leadership.certificates:
                    if last_nonce >= certificate.committed_nonce:
                        expected = certificate.leader
                _, last_nonce, _ = self.replication.verify(envelope, last_nonce, expected)
            if last_nonce != self._committed_position():
                raise ProtocolError("replication log does not match committed position")
        self.ecosystem = Ecosystem()
        self._lock = asyncio.Lock()

    async def dispatch(
        self,
        request: dict[str, Any],
        peer_identity: PeerIdentity | None = None,
    ) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        try:
            if not isinstance(params, dict):
                raise ProtocolError("params must be an object")
            if peer_identity:
                peer_identity.authorize(method)
                if method == "cluster.heartbeat" and params.get("leader") != peer_identity.node_id:
                    raise ProtocolError("heartbeat signer does not match TLS peer")
                if (method == "cluster.timeout_vote"
                        and params.get("vote", {}).get("voter") != peer_identity.node_id):
                    raise ProtocolError("timeout voter does not match TLS peer")
                if method == "cluster.certificate" and params.get("leader") != peer_identity.node_id:
                    raise ProtocolError("certificate sender does not match TLS peer")
            if method == "replica.prepare":
                result = await self.prepare_replica(params)
                return {"id": request_id, "result": result}
            if method == "replica.commit":
                result = await self.commit_replica(params)
                return {"id": request_id, "result": result}
            if method == "replica.abort":
                result = await self.abort_replica(params)
                return {"id": request_id, "result": result}
            if method == "replica.position":
                if (not self.replication or not self.leadership
                        or self.role == self.leadership.leader):
                    raise ProtocolError("node does not expose a replica position")
                return {"id": request_id, "result": {
                    "node_id": self.node_id,
                    "nonce": self._committed_position(),
                }}
            if method == "replica.history":
                if not self.replication or not self.leadership:
                    raise ProtocolError("replication history is not configured")
                async with self._lock:
                    position = self._committed_position()
                    start = params.get("from", -1)
                    if (type(start) is not int or start < 0 or start > position
                            or len(self.replication_log) != position):
                        raise ProtocolError("invalid or incomplete replication history")
                    return {"id": request_id, "result": {
                        "node_id": self.node_id, "nonce": position,
                        "entries": self.replication_log[start:start + 10],
                        "ledger_digest": self._ledger_digest(),
                    }}
            if method == "cluster.heartbeat":
                result = await self.receive_heartbeat(params)
                return {"id": request_id, "result": result}
            if method == "cluster.timeout_vote":
                result = await self.receive_timeout_vote(params)
                return {"id": request_id, "result": result}
            if method == "cluster.certificate":
                result = await self.receive_certificate(params)
                return {"id": request_id, "result": result}
            if method == "cluster.status":
                result = await self.cluster_status()
                return {"id": request_id, "result": result}
            if method == "cluster.leadership":
                if not self.leadership:
                    raise ProtocolError("cluster leadership is not configured")
                return {"id": request_id, "result": self.leadership.snapshot()}
            if method == "cluster.sync":
                result = await self.sync_replicas()
                return {"id": request_id, "result": result}
            if (method in self.MUTATING_METHODS and self.replication
                    and self.leadership and self.leadership.leader == self.node_id):
                self._verify_request_actor(request, method, params)
                result = await self.replicate_mutation({"method": method, "params": params})
                return {"id": request_id, "result": result}
            if method in self.MUTATING_METHODS and self.replication and self.role != "standalone":
                leader = self.leadership.leader
                return {"id": request_id, "error": {
                    "code": "NOT_LEADER", "message": "submit mutations to the certified leader",
                    "leader": leader, "url": self.peer_urls.get(leader),
                }}
            async with self._lock:
                self._verify_request_actor(request, method, params)
                if method == "status":
                    result = self.ledger.snapshot()
                elif method == "ecosystem.demo":
                    result = self.ecosystem.demo()
                elif method == "offer":
                    result = self.ledger.offer(**params).public()
                elif method == "accept":
                    result = self.ledger.accept(**params).public()
                elif method == "commit":
                    result = self.ledger.commit(**params).public()
                elif method == "cancel":
                    result = self.ledger.cancel(**params).public()
                elif method == "advance":
                    result = [b.public() for b in self.ledger.advance(**params)]
                else:
                    raise ProtocolError("unknown method")
                if self.store and method in self.MUTATING_METHODS:
                    replay = self.authenticator.snapshot() if self.authenticator else {}
                    self.store.save(self.ledger, replay, self.replication_nonces)
            return {"id": request_id, "result": result}
        except CommitUncertain as exc:
            return {"id": request_id, "error": {"code": "COMMIT_UNCERTAIN", "message": str(exc)}}
        except (ProtocolError, TypeError) as exc:
            return {"id": request_id, "error": {"code": "INVALID_REQUEST", "message": str(exc)}}

    def _verify_request_actor(self, request: dict, method: str | None, params: dict) -> None:
        if not self.authenticator or method == "status":
            return
        actor = self.authenticator.verify(request)
        actor_field = {
            "offer": "sender",
            "accept": "receiver",
            "commit": "sender",
            "cancel": "actor",
        }.get(method)
        if actor_field and params.get(actor_field) != actor:
            raise ProtocolError("authenticated actor does not match request participant")

    def _apply_mutation(self, mutation: dict[str, Any]) -> Any:
        method = mutation["method"]
        params = mutation.get("params", {})
        if method == "offer":
            return self.ledger.offer(**params).public()
        if method == "accept":
            return self.ledger.accept(**params).public()
        if method == "commit":
            return self.ledger.commit(**params).public()
        if method == "cancel":
            return self.ledger.cancel(**params).public()
        if method == "advance":
            return [branch.public() for branch in self.ledger.advance(**params)]
        raise ProtocolError("unknown replicated mutation")

    def _save(self) -> None:
        if self.store:
            replay = self.authenticator.snapshot() if self.authenticator else {}
            self.store.save(
                self.ledger,
                replay,
                self.replication_nonces,
                self.replication_log,
                self.replication_pending,
                self.leadership.snapshot() if self.leadership else None,
            )

    def _committed_position(self) -> int:
        return max(self.replication_nonces.values(), default=0)

    def _ledger_digest(self) -> str:
        return hashlib.sha256(canonical_json(self.ledger.snapshot())).hexdigest()

    async def receive_heartbeat(self, envelope: dict) -> dict:
        if not self.leadership or not self.leadership.authority.verify_heartbeat(envelope):
            raise ProtocolError("invalid leadership heartbeat")
        async with self._lock:
            if envelope["nonce"] != self._committed_position():
                raise ProtocolError("heartbeat position differs from local replica")
            if envelope["tick"] == self.leadership.last_heartbeat_tick:
                return {"node_id": self.node_id, "term": self.leadership.term}
            self.leadership.heartbeat(
                envelope["leader"], envelope["term"], envelope["tick"], envelope["nonce"]
            )
            self._save()
            return {"node_id": self.node_id, "term": self.leadership.term}

    async def receive_timeout_vote(self, params: dict) -> dict:
        if not self.leadership:
            raise ProtocolError("cluster leadership is not configured")
        try:
            vote = FailureVote(**params["vote"])
            digest = params["ledger_digest"]
        except (KeyError, TypeError) as exc:
            raise ProtocolError("invalid timeout vote request") from exc
        async with self._lock:
            state = self.leadership
            if state.leader == "tertiary":
                raise ProtocolError("ordered leadership is exhausted")
            successor = state._successor()
            witness = next(
                role for role in ROLE_ORDER if role not in {state.leader, successor}
            )
            if (
                self.node_id != witness or vote.voter != successor
                or not state.authority.verify(vote)
                or vote.term != state.term + 1 or vote.accused != state.leader
                or vote.candidate != successor
                or vote.observed_tick - state.last_heartbeat_tick < state.timeout_ticks
                or abs(vote.observed_tick - int(time.time() // 2)) > 1
                or vote.committed_nonce != self._committed_position()
                or self.replication_pending is not None
                or len(self.replication_log) != self._committed_position()
                or digest != self._ledger_digest()
            ):
                raise ProtocolError("timeout vote lacks a matching clean replica")
            response = state.authority.vote(
                self.node_id, vote.term, vote.accused, vote.candidate,
                vote.observed_tick, vote.committed_nonce,
            )
            return asdict(response)

    async def receive_certificate(self, params: dict) -> dict:
        try:
            certificate = LeadershipCertificate(
                term=params["term"], leader=params["leader"],
                committed_nonce=params["committed_nonce"],
                votes=tuple(FailureVote(**value) for value in params["votes"]),
                digest=params["digest"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid leadership certificate") from exc
        await self.record_leadership_certificate(certificate)
        return {"node_id": self.node_id, "term": self.leadership.term}

    async def failover_step(self, tick: int | None = None) -> None:
        """One conservative heartbeat/election step for the research cluster."""
        if not self.leadership:
            return
        tick = int(time.time() // 2) if tick is None else tick
        state = self.leadership
        if state.last_heartbeat_tick == 0:
            state.last_heartbeat_tick = tick
            self._save()
        if state.leader == self.node_id:
            async with self._lock:
                if tick > state.last_heartbeat_tick:
                    state.heartbeat(self.node_id, state.term, tick, self._committed_position())
                    self._save()
                heartbeat = state.authority.sign_heartbeat(
                    self.node_id, state.term, tick, self._committed_position()
                )
                certificate = asdict(state.certificates[-1]) if state.certificates else None
            if certificate:
                await asyncio.gather(*(
                    self._replica_rpc(url, "cluster.certificate", certificate)
                    for url in self.peer_urls.values()
                ))
            await asyncio.gather(*(
                self._replica_rpc(url, "cluster.heartbeat", heartbeat)
                for url in self.peer_urls.values()
            ))
            return
        if state.leader == "tertiary" or self.node_id != state._successor():
            return
        if tick - state.last_heartbeat_tick < state.timeout_ticks:
            return
        witness = next(role for role in ROLE_ORDER if role not in {state.leader, self.node_id})
        witness_url = self.peer_urls.get(witness)
        if not witness_url:
            return
        if not await self._reconcile_with_witness(witness_url):
            return
        # Recover a certificate the witness persisted if its acknowledgement was lost.
        remote = await self._replica_rpc(witness_url, "cluster.leadership", {})
        if remote and "result" in remote and remote["result"].get("term") == state.term + 1:
            try:
                restored = LeadershipState.from_snapshot(state.authority, remote["result"])
                await self.record_leadership_certificate(restored.certificates[-1])
            except (ProtocolError, IndexError):
                pass
            return
        vote = state.authority.vote(
            self.node_id, state.term + 1, state.leader, self.node_id,
            tick, self._committed_position(),
        )
        answer = await self._replica_rpc(witness_url, "cluster.timeout_vote", {
            "vote": asdict(vote), "ledger_digest": self._ledger_digest(),
        })
        if not answer or "result" not in answer:
            return
        try:
            signed = FailureVote(**answer["result"])
            trial = LeadershipState.from_snapshot(state.authority, state.snapshot())
            trial.submit(vote)
            certificate = trial.submit(signed)
        except (ProtocolError, TypeError):
            return
        accepted = await self._replica_rpc(
            witness_url, "cluster.certificate", asdict(certificate)
        )
        if accepted and "result" in accepted:
            await self.record_leadership_certificate(certificate)

    async def record_leadership_certificate(self, certificate: LeadershipCertificate) -> None:
        """Persist a validated transition and fence the former leader."""
        if not self.leadership:
            raise ProtocolError("cluster leadership is not configured")
        async with self._lock:
            if self.leadership.certificates and certificate == self.leadership.certificates[-1]:
                return
            if self.leadership.committed_nonce != self._committed_position():
                raise ProtocolError("leadership position is behind the replica")
            self.leadership.accept_certificate(certificate)
            # A prepared mutation without a committed decision cannot cross terms.
            self.replication_pending = None
            self._save()

    def _verify_replica_envelope(self, envelope: dict) -> tuple[str, int, dict]:
        if (self.leadership and self.leadership.leader != "primary"
                and envelope.get("leader") == "primary"):
            raise ProtocolError("old Primary is fenced by a leadership certificate")
        if not self.replication or self.role not in ROLE_ORDER or self.role == self.leadership.leader:
            raise ProtocolError("node does not accept replicated mutations")
        return self.replication.verify(
            envelope, self._committed_position(), self.leadership.leader
        )

    async def prepare_replica(self, envelope: dict) -> Any:
        async with self._lock:
            _, nonce, mutation = self._verify_replica_envelope(envelope)
            if self.replication_pending:
                if self.replication_pending == envelope:
                    return {"node_id": self.node_id, "nonce": nonce, "state": "prepared"}
                raise ProtocolError("replica already has a different prepared mutation")
            trial = Ledger.from_snapshot(self.ledger.snapshot())
            original = self.ledger
            self.ledger = trial
            try:
                self._apply_mutation(mutation)
            finally:
                self.ledger = original
            self.replication_pending = envelope
            self._save()
            return {"node_id": self.node_id, "nonce": nonce, "state": "prepared"}

    async def commit_replica(self, envelope: dict) -> Any:
        async with self._lock:
            leader, nonce, mutation = self._verify_replica_envelope(envelope)
            if self.replication_pending != envelope:
                raise ProtocolError("replica commit does not match its prepared mutation")
            result = self._apply_mutation(mutation)
            self.replication_nonces[leader] = nonce
            self.replication_log.append(envelope)
            if self.leadership:
                self.leadership.committed_nonce = nonce
            self.replication_pending = None
            self._save()
            return {"node_id": self.node_id, "nonce": nonce, "mutation": result}

    async def abort_replica(self, envelope: dict) -> Any:
        async with self._lock:
            _, nonce, _ = self._verify_replica_envelope(envelope)
            if self.replication_pending and self.replication_pending != envelope:
                raise ProtocolError("replica abort does not match its prepared mutation")
            self.replication_pending = None
            self._save()
            return {"node_id": self.node_id, "nonce": nonce, "state": "aborted"}

    async def replicate_mutation(self, mutation: dict[str, Any]) -> Any:
        if not self.replication:
            raise ProtocolError("cluster replication is not configured")
        if self.leadership and self.leadership.leader != self.node_id:
            raise ProtocolError("old Primary is fenced by a leadership certificate")
        async with self._lock:
            await self._abort_uncommitted_primary()
            trial = Ledger.from_snapshot(self.ledger.snapshot())
            original = self.ledger
            self.ledger = trial
            try:
                self._apply_mutation(mutation)
            finally:
                self.ledger = original
            nonce = self._committed_position() + 1
            envelope = self.replication.sign(self.node_id, nonce, mutation)
            self.replication_pending = envelope
            self._save()
            acknowledgements = sum(await asyncio.gather(
                *(self._sync_and_prepare(url, envelope) for _, url in sorted(self.peer_urls.items()))
            ))
            if acknowledgements < 1:
                await asyncio.gather(*(
                    self._replica_rpc(url, "replica.abort", envelope)
                    for _, url in sorted(self.peer_urls.items())
                ))
                self.replication_pending = None
                self._save()
                raise ProtocolError("mutation did not receive a 2/3 cluster quorum")
            result = self._apply_mutation(mutation)
            self.replication_nonces[self.node_id] = nonce
            if self.leadership:
                self.leadership.committed_nonce = nonce
            self.replication_log.append(envelope)
            self.replication_pending = None
            self._save()
            commit_responses = await asyncio.gather(*(
                self._replica_rpc(url, "replica.commit", envelope)
                for _, url in sorted(self.peer_urls.items())
            ))
            if not any(
                response and response.get("result", {}).get("nonce") == nonce
                for response in commit_responses
            ):
                raise CommitUncertain(
                    "commit outcome is uncertain: Primary committed locally but no replica "
                    "confirmed; inspect cluster state before retrying"
                )
            return result

    async def _replica_rpc(self, url: str, method: str, params: dict) -> dict | None:
        import websockets

        try:
            async with asyncio.timeout(3):
                async with websockets.connect(
                    url, max_size=64 * 1024,
                    ssl=self._peer_ssl if url.startswith("wss://") else None,
                ) as socket:
                    await socket.send(json.dumps({"id": method, "method": method, "params": params}))
                    return json.loads(await socket.recv())
        except (OSError, TimeoutError, ValueError):
            return None

    async def _sync_history(self, url: str) -> bool:
        position = await self._replica_rpc(url, "replica.position", {})
        if not position or "result" not in position:
            return False
        try:
            nonce = int(position["result"].get("nonce", -1))
        except (TypeError, ValueError):
            return False
        leader_position = self._committed_position()
        if nonce < 0 or nonce > leader_position or len(self.replication_log) != leader_position:
            return False
        for historical in self.replication_log[nonce:]:
            prepared = await self._replica_rpc(url, "replica.prepare", historical)
            committed = await self._replica_rpc(url, "replica.commit", historical)
            if not prepared or "result" not in prepared or not committed or "result" not in committed:
                return False
        return True

    async def _sync_from_peer(self, url: str) -> bool:
        """Recover only independently signed committed entries from a full peer log."""
        for _ in range(10):
            local_position = self._committed_position()
            response = await self._replica_rpc(url, "replica.history", {"from": local_position})
            if not response or "result" not in response:
                return False
            remote = response["result"]
            try:
                remote_position = int(remote["nonce"])
                entries = remote["entries"]
                digest = remote["ledger_digest"]
            except (KeyError, TypeError, ValueError):
                return False
            if remote_position < local_position or not isinstance(entries, list):
                return False
            if remote_position == local_position:
                return digest == self._ledger_digest() and self.replication_pending is None
            if not entries:
                return False
            for envelope in entries:
                if self.replication_pending is not None and self.replication_pending != envelope:
                    return False
                try:
                    if self.replication_pending is None:
                        await self.prepare_replica(envelope)
                    await self.commit_replica(envelope)
                except (ProtocolError, TypeError):
                    return False
        return False

    async def _reconcile_with_witness(self, url: str) -> bool:
        remote = await self._replica_rpc(url, "replica.position", {})
        if not remote or "result" not in remote:
            return False
        try:
            remote_position = int(remote["result"]["nonce"])
        except (KeyError, TypeError, ValueError):
            return False
        if remote_position > self._committed_position():
            if not await self._sync_from_peer(url):
                return False
        elif self.replication_pending is not None:
            # Neither survivor has a committed decision for the prepared record.
            return False
        if (self.replication_pending is not None
                or len(self.replication_log) != self._committed_position()):
            return False
        if remote_position < self._committed_position() and not await self._sync_history(url):
            return False
        confirmed = await self._replica_rpc(url, "replica.history", {
            "from": self._committed_position()
        })
        return bool(
            confirmed and "result" in confirmed
            and confirmed["result"].get("nonce") == self._committed_position()
            and confirmed["result"].get("ledger_digest") == self._ledger_digest()
        )

    async def _sync_and_prepare(self, url: str, envelope: dict) -> bool:
        if not await self._sync_history(url):
            return False
        response = await self._replica_rpc(url, "replica.prepare", envelope)
        return bool(response and "result" in response)

    async def _abort_uncommitted_primary(self) -> None:
        if not self.replication_pending:
            return
        envelope = self.replication_pending
        await asyncio.gather(*(
            self._replica_rpc(url, "replica.abort", envelope)
            for _, url in sorted(self.peer_urls.items())
        ))
        self.replication_pending = None
        self._save()

    async def sync_replicas(self) -> dict[str, str]:
        if not self.replication or not self.leadership or self.leadership.leader != self.node_id:
            raise ProtocolError("only the certified leader can synchronize replicas")
        async with self._lock:
            await self._abort_uncommitted_primary()
            results = await asyncio.gather(*(
                self._sync_history(url) for _, url in sorted(self.peer_urls.items())
            ))
        return {
            node_id: "synchronized" if ok else "unavailable"
            for (node_id, _), ok in zip(sorted(self.peer_urls.items()), results, strict=True)
        }

    async def cluster_status(self) -> dict[str, Any]:
        import websockets

        async with self._lock:
            local = self.ledger.snapshot()

        async def probe(node_id: str, url: str) -> tuple[str, dict[str, Any]]:
            try:
                async with asyncio.timeout(3):
                    async with websockets.connect(
                        url, max_size=64 * 1024,
                        ssl=self._peer_ssl if url.startswith("wss://") else None,
                    ) as socket:
                        await socket.send(json.dumps({
                            "id": f"cluster-{self.node_id}",
                            "method": "status",
                            "params": {},
                        }))
                        response = json.loads(await socket.recv())
                if "result" not in response:
                    raise ProtocolError("peer returned an RPC error")
                return node_id, {"status": "available", "ledger": response["result"]}
            except (OSError, TimeoutError, ValueError, ProtocolError):
                return node_id, {"status": "unavailable"}

        results = await asyncio.gather(
            *(probe(node_id, url) for node_id, url in sorted(self.peer_urls.items()))
        )
        return {"node_id": self.node_id, "local": local, "peers": dict(results)}

    async def handler(self, websocket: Any) -> None:
        peer_identity = None
        if self.peer_registry:
            transport = getattr(websocket, "transport", None)
            ssl_object = transport.get_extra_info("ssl_object") if transport else None
            certificate = ssl_object.getpeercert(binary_form=True) if ssl_object else None
            peer_identity = self.peer_registry.verify_der(certificate)
        async for raw in websocket:
            try:
                request = json.loads(raw)
                if not isinstance(request, dict):
                    raise TypeError("request must be an object")
                response = await self.dispatch(request, peer_identity)
            except (json.JSONDecodeError, TypeError) as exc:
                response = {"id": None, "error": {"code": "INVALID_JSON", "message": str(exc)}}
            await websocket.send(json.dumps(response, sort_keys=True))


async def serve(
    host: str,
    port: int,
    state_path: str | None = None,
    tls: TLSMaterial | None = None,
    peer_registry: PeerRegistry | None = None,
    node_id: str = "local",
    peer_urls: dict[str, str] | None = None,
    role: str = "standalone",
    cluster_secret: str | None = None,
    keyring: NodeKeyring | None = None,
) -> None:
    import websockets

    node = ReferenceNode(
        state_path=state_path,
        peer_registry=peer_registry,
        node_id=node_id,
        peer_urls=peer_urls,
        role=role,
        cluster_secret=cluster_secret,
        keyring=keyring,
        tls=tls,
    )
    ssl_context = tls.server_context() if tls else None
    async with websockets.serve(
        node.handler, host, port, max_size=64 * 1024, ssl=ssl_context
    ):
        scheme = "wss" if tls else "ws"
        print(f"splitd listening on {scheme}://{host}:{port}")
        async def run_leadership() -> None:
            while True:
                try:
                    await node.failover_step()
                except ProtocolError as exc:
                    print(f"leadership step rejected: {exc}")
                await asyncio.sleep(2)

        if node.leadership:
            await run_leadership()
        else:
            await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="SplitChain experimental reference node")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state", help="durable JSON ledger state path")
    parser.add_argument("--node-id", default="local", help="unique node identifier")
    parser.add_argument(
        "--role",
        choices=("standalone", "primary", "secondary", "tertiary"),
        default="standalone",
    )
    parser.add_argument(
        "--peer",
        action="append",
        default=[],
        help="peer endpoint as NODE_ID=ws[s]://HOST:PORT; repeat for multiple peers",
    )
    parser.add_argument("--tls-cert", help="PEM node certificate")
    parser.add_argument("--tls-key", help="PEM node private key")
    parser.add_argument("--tls-ca", help="PEM certificate authority used to verify clients")
    parser.add_argument("--node-key", help="PEM Ed25519 private signing key for this node")
    parser.add_argument("--peer-keys", help="JSON registry of the three node public keys")
    parser.add_argument(
        "--tls-peers",
        help="JSON registry binding authorized certificate fingerprints to node identities",
    )
    args = parser.parse_args()
    tls = TLSMaterial.from_values(args.tls_cert, args.tls_key, args.tls_ca)
    if args.tls_peers and not tls:
        parser.error("--tls-peers requires TLS")
    peers = PeerRegistry.from_path(args.tls_peers) if args.tls_peers else None
    try:
        peer_urls = parse_peer_values(args.peer)
    except ProtocolError as exc:
        parser.error(str(exc))
    cluster_secret = os.environ.get("SPLITCHAIN_CLUSTER_SECRET")
    if bool(args.node_key) != bool(args.peer_keys):
        parser.error("--node-key and --peer-keys must be provided together")
    if args.node_key and cluster_secret:
        parser.error("node keys cannot be combined with SPLITCHAIN_CLUSTER_SECRET")
    if args.node_key and (
        not tls or not peers
        or set(peer_urls) != set(ROLE_ORDER) - {args.role}
        or any(not peers.has_node_role(role, role) for role in peer_urls)
        or any(not url.startswith("wss://") for url in peer_urls.values())
    ):
        parser.error("node-key mode requires mTLS, peer registry, and both wss peers")
    if args.role != "standalone" and not cluster_secret and not args.node_key:
        parser.error("cluster roles require a shared demo secret or independent node keys")
    try:
        keyring = NodeKeyring.from_files(args.role, args.node_key, args.peer_keys) if args.node_key else None
    except ProtocolError as exc:
        parser.error(str(exc))
    asyncio.run(serve(
        args.host, args.port, args.state, tls, peers, args.node_id, peer_urls,
        args.role, cluster_secret, keyring,
    ))


if __name__ == "__main__":
    main()
