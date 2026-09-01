"""Minimal JSON-over-WebSocket reference node for local experiments."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar

from .auth import RequestAuthenticator
from .ecosystem import Ecosystem
from .model import Ledger, ProtocolError
from .persistence import LedgerStore
from .transport import PeerIdentity, PeerRegistry, TLSMaterial


class ReferenceNode:
    MUTATING_METHODS: ClassVar[set[str]] = {"offer", "accept", "commit", "cancel", "advance"}

    def __init__(
        self,
        balances: dict[str, int] | None = None,
        state_path: str | Path | None = None,
        auth_secrets: dict[str, str] | None = None,
        peer_registry: PeerRegistry | None = None,
    ) -> None:
        initial = balances or {"alice": 1_000, "bob": 1_000}
        self.store = LedgerStore(state_path) if state_path else None
        self.authenticator = RequestAuthenticator(auth_secrets) if auth_secrets else None
        self.peer_registry = peer_registry
        if self.store:
            self.ledger, replay_nonces = self.store.load_node_state(initial)
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
            async with self._lock:
                if peer_identity:
                    peer_identity.authorize(method)
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
                    self.store.save(self.ledger, replay)
            return {"id": request_id, "result": result}
        except (ProtocolError, TypeError) as exc:
            return {"id": request_id, "error": {"code": "INVALID_REQUEST", "message": str(exc)}}

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
) -> None:
    import websockets

    node = ReferenceNode(state_path=state_path, peer_registry=peer_registry)
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
    asyncio.run(serve(args.host, args.port, args.state, tls, peers))


if __name__ == "__main__":
    main()
