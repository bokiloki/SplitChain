"""Authenticated peer relay; only the per-node sandbox stores consensus records."""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import websockets

from .clock_heartbeat import RoundHeartbeat
from .model import ProtocolError, protocol_digest
from .sandbox_daemon import sandbox_request
from .stake_votes import StakeCertificate, StakeDecision, StakeVote
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
    def _event(message: dict) -> tuple[str, BetCommit | BetSuffixReveal
                                       | StakeCertificate | RoundHeartbeat,
                                       int | None, tuple[tuple[str, int], ...] | None]:
        if not isinstance(message, dict):
            raise ProtocolError("invalid peer bet event")
        method = message.get("method")
        if method == "bet.commit":
            event = BetCommit(**message["event"])
        elif method == "bet.reveal_suffix":
            event = BetSuffixReveal(**message["event"])
        elif method == "bet.block.accept":
            raw = message["event"]
            event = StakeCertificate(StakeDecision(**raw["decision"]), tuple(
                StakeVote(item["voter"], StakeDecision(**item["decision"]),
                          item["signature"]) for item in raw["votes"]
            ))
        elif method == "clock.heartbeat":
            event = RoundHeartbeat(**message["event"])
        elif method == "round.advance.vote":
            raw = message["event"]
            event = StakeVote(raw["voter"], StakeDecision(**raw["decision"]), raw["signature"])
        else:
            raise ProtocolError("unsupported peer bet event")
        round_number = message.get("round")
        if method in {"bet.commit", "bet.reveal_suffix"} and type(round_number) is not int:
            raise ProtocolError("bet event requires a consensus round")
        slots = (tuple(tuple(slot) for slot in message["slots"])
                 if method == "bet.block.accept" else None)
        return method, event, round_number, slots

    async def _store_and_forward(self, message: dict, sender: str | None) -> dict:
        method, event, round_number, slots = self._event(message)
        result = await sandbox_request(self.socket_path, method, event, round_number, slots)
        digest = protocol_digest("splitchain/peer-bet-event/v1", message)
        if digest not in self.seen:
            self.seen.add(digest)
            await asyncio.gather(*(
                self._send(peer, message, digest)
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

    async def publish(self, event: BetCommit | BetSuffixReveal | StakeCertificate
                      | RoundHeartbeat, round_number: int | None = None,
                      slots: tuple[tuple[str, int], ...] | None = None) -> dict:
        if isinstance(event, BetCommit):
            method = "bet.commit"
        elif isinstance(event, BetSuffixReveal):
            method = "bet.reveal_suffix"
        elif isinstance(event, StakeCertificate):
            method = "bet.block.accept"
        elif isinstance(event, RoundHeartbeat):
            method = "clock.heartbeat"
        elif isinstance(event, StakeVote):
            method = "round.advance.vote"
        else:
            raise ProtocolError("unsupported peer event")
        message: dict = {"method": method, "event": asdict(event)}
        if round_number is not None:
            message["round"] = round_number
        if slots is not None:
            message["slots"] = slots
        return await self._store_and_forward(message, sender=None)

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


async def run_relay(relay: BetPeerRelay, host: str, port: int,
                    heartbeat_seconds: float = 2.0) -> None:
    async def pulse() -> None:
        while True:
            try:
                raw = await sandbox_request(relay.socket_path, "clock.create")
                await relay.publish(RoundHeartbeat(**raw))
                await relay.retry_pending()
            except (OSError, ProtocolError, TimeoutError):
                # A local sandbox or peer failure must not invent a round.
                pass
            await asyncio.sleep(heartbeat_seconds)

    if heartbeat_seconds <= 0:
        raise ProtocolError("heartbeat interval must be positive")
    async with websockets.serve(
        relay.handler, host, port, ssl=relay.tls.server_context(),
        max_size=16 * 1024,
    ):
        await pulse()


def main() -> None:
    parser = argparse.ArgumentParser(description="Relay signed events to isolated node sandboxes")
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--peer", action="append", default=[], help="NODE_ID=wss://HOST:PORT")
    parser.add_argument("--registry", required=True)
    parser.add_argument("--tls-cert", required=True)
    parser.add_argument("--tls-key", required=True)
    parser.add_argument("--tls-ca", required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    peers: dict[str, str] = {}
    for item in args.peer:
        name, sep, url = item.partition("=")
        if not sep or name in peers:
            raise ProtocolError("duplicate or invalid relay peer")
        peers[name] = url
    tls = TLSMaterial.from_values(args.tls_cert, args.tls_key, args.tls_ca)
    relay = BetPeerRelay(args.node_id, args.socket, peers,
                         PeerRegistry.from_path(args.registry), tls)
    asyncio.run(run_relay(relay, args.host, args.port))


if __name__ == "__main__":
    main()
