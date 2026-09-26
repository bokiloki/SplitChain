"""Authenticated peer relay; only the per-node sandbox stores consensus records."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import websockets

from .model import ProtocolError, protocol_digest
from .sandbox_daemon import sandbox_request
from .timestamp_bets import BetCommit, BetSuffixReveal
from .transport import PeerRegistry, TLSMaterial


class BetPeerRelay:
    def __init__(self, node_id: str, socket_path: str | Path,
                 peers: dict[str, str], registry: PeerRegistry, tls: TLSMaterial) -> None:
        if not node_id or node_id in peers or any(not url.startswith("wss://") for url in peers.values()):
            raise ProtocolError("bet relay requires distinct mutually authenticated peers")
        self.node_id = node_id
        self.socket_path = Path(socket_path)
        self.peers = peers.copy()
        self.registry = registry
        self.tls = tls
        self.pending: dict[tuple[str, str], dict] = {}
        self.seen: set[str] = set()

    @staticmethod
    def _event(message: dict) -> tuple[str, BetCommit | BetSuffixReveal, int]:
        if not isinstance(message, dict) or type(message.get("round")) is not int:
            raise ProtocolError("invalid peer bet event")
        method = message.get("method")
        if method == "bet.commit":
            event = BetCommit(**message["event"])
        elif method == "bet.reveal_suffix":
            event = BetSuffixReveal(**message["event"])
        else:
            raise ProtocolError("unsupported peer bet event")
        return method, event, message["round"]

    async def _store_and_forward(self, message: dict, sender: str | None) -> dict:
        method, event, round_number = self._event(message)
        result = await sandbox_request(self.socket_path, method, event, round_number)
        digest = protocol_digest("splitchain/peer-bet-event/v1", message)
        if digest not in self.seen:
            self.seen.add(digest)
            await asyncio.gather(*(
                self._send(peer, {"method": method, "event": asdict(event),
                                  "round": round_number}, digest)
                for peer in self.peers if peer != sender
            ))
        return result

    async def _send(self, peer: str, message: dict, digest: str) -> None:
        try:
            async with asyncio.timeout(4):
                async with websockets.connect(
                    self.peers[peer], ssl=self.tls.client_context(),
                    max_size=16 * 1024, open_timeout=3,
                ) as socket:
                    tls_object = socket.transport.get_extra_info("ssl_object")
                    identity = self.registry.verify_der(
                        tls_object.getpeercert(binary_form=True) if tls_object else None,
                    )
                    if identity.node_id != peer:
                        raise ProtocolError("peer certificate differs from configured node")
                    await socket.send(json.dumps(message, sort_keys=True))
                    reply = json.loads(await socket.recv())
                    if reply.get("error") or reply.get("result") is None:
                        raise ProtocolError("peer rejected signed bet event")
            self.pending.pop((peer, digest), None)
        except (OSError, TimeoutError, ValueError, websockets.WebSocketException):
            self.pending[(peer, digest)] = message

    async def retry_pending(self) -> None:
        await asyncio.gather(*(
            self._send(peer, message, digest)
            for (peer, digest), message in list(self.pending.items())
        ))

    async def publish(self, event: BetCommit | BetSuffixReveal, round_number: int) -> dict:
        method = "bet.commit" if isinstance(event, BetCommit) else "bet.reveal_suffix"
        return await self._store_and_forward({
            "method": method, "event": asdict(event), "round": round_number,
        }, sender=None)

    async def handler(self, socket: Any) -> None:
        tls_object = socket.transport.get_extra_info("ssl_object")
        identity = self.registry.verify_der(
            tls_object.getpeercert(binary_form=True) if tls_object else None,
        )
        if identity.node_id not in self.peers:
            raise ProtocolError("unauthorized bet relay peer")
        async for raw in socket:
            try:
                result = await self._store_and_forward(json.loads(raw), sender=identity.node_id)
                response = {"result": result}
            except (ProtocolError, KeyError, TypeError, ValueError) as exc:
                response = {"error": str(exc)}
            await socket.send(json.dumps(response, sort_keys=True))
