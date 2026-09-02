"""Minimal JSON-over-WebSocket reference node for local experiments."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

from .auth import RequestAuthenticator
from .ecosystem import Ecosystem
from .model import Ledger, ProtocolError
from .persistence import LedgerStore
from .replication import ReplicationAuthenticator
from .transport import PeerIdentity, PeerRegistry, TLSMaterial


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
    ) -> None:
        initial = balances or {"alice": 1_000, "bob": 1_000}
        self.store = LedgerStore(state_path) if state_path else None
        self.authenticator = RequestAuthenticator(auth_secrets) if auth_secrets else None
        self.peer_registry = peer_registry
        self.node_id = node_id
        self.peer_urls = dict(peer_urls or {})
        self.role = role
        self.replication = ReplicationAuthenticator(cluster_secret) if cluster_secret else None
        self.replication_nonces: dict[str, int] = {}
        if self.store:
            self.ledger, replay_nonces, self.replication_nonces = self.store.load_full_node_state(
                initial
            )
            if self.authenticator:
                self.authenticator.restore(replay_nonces)
        else:
            self.ledger = Ledger(initial)
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
            if method == "replica.apply":
                result = await self.apply_replica(params)
                return {"id": request_id, "result": result}
            if peer_identity:
                peer_identity.authorize(method)
            if method == "cluster.status":
                result = await self.cluster_status()
                return {"id": request_id, "result": result}
            if method in self.MUTATING_METHODS and self.replication and self.role == "primary":
                result = await self.replicate_mutation({"method": method, "params": params})
                return {"id": request_id, "result": result}
            if method in self.MUTATING_METHODS and self.replication and self.role != "standalone":
                raise ProtocolError("mutations must be submitted to the Primary node")
            async with self._lock:
                if self.authenticator and method != "status":
                    actor = self.authenticator.verify(request)
                    actor_fields = {
                        "offer": "sender",
                        "accept": "receiver",
                        "commit": "sender",
                        "cancel": "actor",
                    }
                    actor_field = actor_fields.get(method)
                    if actor_field and params.get(actor_field) != actor:
                        raise ProtocolError("authenticated actor does not match request participant")
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
        except (ProtocolError, TypeError) as exc:
            return {"id": request_id, "error": {"code": "INVALID_REQUEST", "message": str(exc)}}

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
            self.store.save(self.ledger, replay, self.replication_nonces)

    async def apply_replica(self, envelope: dict) -> Any:
        if not self.replication or self.role not in {"secondary", "tertiary"}:
            raise ProtocolError("node does not accept replicated mutations")
        async with self._lock:
            leader, nonce, mutation = self.replication.verify(
                envelope, self.replication_nonces.get("primary", 0)
            )
            trial = Ledger.from_snapshot(self.ledger.snapshot())
            original = self.ledger
            self.ledger = trial
            try:
                result = self._apply_mutation(mutation)
            except Exception:
                self.ledger = original
                raise
            self.replication_nonces[leader] = nonce
            self._save()
            return {"node_id": self.node_id, "nonce": nonce, "mutation": result}

    async def replicate_mutation(self, mutation: dict[str, Any]) -> Any:
        import websockets

        if not self.replication:
            raise ProtocolError("cluster replication is not configured")
        async with self._lock:
            trial = Ledger.from_snapshot(self.ledger.snapshot())
            original = self.ledger
            self.ledger = trial
            try:
                self._apply_mutation(mutation)
            finally:
                self.ledger = original
            nonce = self.replication_nonces.get(self.node_id, 0) + 1
            envelope = self.replication.sign(self.node_id, nonce, mutation)

            async def send(url: str) -> bool:
                try:
                    async with asyncio.timeout(3):
                        async with websockets.connect(url, max_size=64 * 1024) as socket:
                            await socket.send(json.dumps({
                                "id": f"replica-{nonce}",
                                "method": "replica.apply",
                                "params": envelope,
                            }))
                            response = json.loads(await socket.recv())
                    return "result" in response
                except (OSError, TimeoutError, ValueError):
                    return False

            acknowledgements = sum(await asyncio.gather(
                *(send(url) for _, url in sorted(self.peer_urls.items()))
            ))
            if acknowledgements < 1:
                raise ProtocolError("mutation did not receive a 2/3 cluster quorum")
            result = self._apply_mutation(mutation)
            self.replication_nonces[self.node_id] = nonce
            self._save()
            return result

    async def cluster_status(self) -> dict[str, Any]:
        import websockets

        async with self._lock:
            local = self.ledger.snapshot()

        async def probe(node_id: str, url: str) -> tuple[str, dict[str, Any]]:
            try:
                async with asyncio.timeout(3):
                    async with websockets.connect(url, max_size=64 * 1024) as socket:
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
) -> None:
    import websockets

    node = ReferenceNode(
        state_path=state_path,
        peer_registry=peer_registry,
        node_id=node_id,
        peer_urls=peer_urls,
        role=role,
        cluster_secret=cluster_secret,
    )
    ssl_context = tls.server_context() if tls else None
    async with websockets.serve(
        node.handler, host, port, max_size=64 * 1024, ssl=ssl_context
    ):
        scheme = "wss" if tls else "ws"
        print(f"splitd listening on {scheme}://{host}:{port}")
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
    if args.role != "standalone" and not cluster_secret:
        parser.error("cluster roles require SPLITCHAIN_CLUSTER_SECRET")
    asyncio.run(serve(
        args.host, args.port, args.state, tls, peers, args.node_id, peer_urls,
        args.role, cluster_secret,
    ))


if __name__ == "__main__":
    main()
