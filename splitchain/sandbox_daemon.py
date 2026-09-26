"""Local-only Unix socket for each validator's independent consensus sandbox."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .clock_heartbeat import ClockTracker, RoundHeartbeat, sign_heartbeat
from .model import GenesisConfig, Ledger, ProtocolError, protocol_digest
from .stake_manifest import load_manifest
from .stake_vault import SandboxConsensusStore
from .stake_votes import StakeCertificate, StakeDecision, StakeVote, StakeVoteBook, sign_vote
from .timestamp_bets import BetCommit, BetSuffixReveal, TimestampBetBook

MAX_REQUEST = 16 * 1024


class SandboxDaemon:
    def __init__(self, socket_path: str | Path, store: SandboxConsensusStore,
                 node_id: str | None = None,
                 private_key: Ed25519PrivateKey | None = None) -> None:
        self.socket_path = Path(socket_path)
        self.store = store
        if store.path.exists():
            self.ledger, self.votes, self.position, self.bets, self.clock = store.load()
        else:
            self.ledger = Ledger(genesis=store.genesis)
            self.votes = StakeVoteBook(store.membership, store.genesis.network_id,
                                       store.genesis.digest(), store.keys)
            self.position = 0
            self.bets = TimestampBetBook(self.votes)
            self.clock = ClockTracker(self.votes)
        self.lock = asyncio.Lock()
        if (node_id is None) != (private_key is None):
            raise ProtocolError("heartbeat signing requires local node identity and private key")
        if node_id is not None:
            known = store.keys.get(node_id)
            if (known is None or private_key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw,
            ) != known.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)):
                raise ProtocolError("local heartbeat key differs from pinned epoch")
        self.node_id = node_id
        self.private_key = private_key

    def round_challenge(self) -> StakeDecision:
        return StakeDecision(
            self.store.genesis.network_id, self.store.membership.epoch,
            self.votes.epoch_digest, "round", self.position + 1,
            protocol_digest("splitchain/sandbox-round/v1", {
                "round": self.ledger.round + 1,
                "ledger": self.ledger.snapshot(),
            }), 1,
        )

    async def dispatch(self, request: dict) -> dict:
        if not isinstance(request, dict):
            raise ProtocolError("sandbox request must be an object")
        method = request.get("method")
        async with self.lock:
            if method == "round.advance.vote":
                event = request["event"]
                vote = StakeVote(event["voter"], StakeDecision(**event["decision"]),
                                 event["signature"])
                expected = self.round_challenge()
                if vote.decision != expected:
                    raise ProtocolError("round vote does not match local consensus state")
                trial = StakeVoteBook.from_snapshot(
                    self.store.membership, self.store.genesis.network_id,
                    self.store.genesis.digest(), self.store.keys,
                    self.votes.snapshot(),
                )
                reached = trial.submit(vote)
                new_ledger = Ledger.from_snapshot(
                    self.ledger.snapshot(), expected_genesis=self.store.genesis,
                )
                if reached:
                    trial.certificate(expected).verify(trial)
                    new_ledger.advance(1)
                self.store.save(new_ledger, trial, self.position + int(reached),
                                self.bets, self.clock)
                self.votes = trial
                self.ledger = new_ledger
                self.bets.votes = trial
                self.position += int(reached)
                return {"quorum": reached, "round": self.ledger.round}
            if method == "round.sign":
                if self.node_id is None or self.private_key is None:
                    raise ProtocolError("local round signing is not configured")
                return asdict(sign_vote(self.private_key, self.node_id,
                                        self.round_challenge()))
            if method == "bet.block.accept":
                raw = request["event"]
                cert = StakeCertificate(StakeDecision(**raw["decision"]), tuple(
                    StakeVote(item["voter"], StakeDecision(**item["decision"]),
                              item["signature"]) for item in raw["votes"]
                ))
                slots = tuple(tuple(item) for item in request["slots"])
                trial = TimestampBetBook.from_snapshot(self.votes, self.bets.snapshot())
                trial.accept_block(slots, cert)
                self.store.save(self.ledger, self.votes, self.position, trial, self.clock)
                self.bets = trial
                return {"accepted": True, "block_height": len(trial.blocks)}
            if method == "clock.heartbeat":
                event = RoundHeartbeat(**request["event"])
                trial = ClockTracker.from_snapshot(self.votes, self.clock.snapshot())
                skewed = trial.observe(event, time.time_ns() // 1_000_000)
                self.store.save(self.ledger, self.votes, self.position, self.bets, trial)
                self.clock = trial
                return {"observed": True, "clock_skewed": skewed,
                        "reported_round": event.round}
            if method == "clock.create":
                if self.node_id is None or self.private_key is None:
                    raise ProtocolError("local heartbeat signing is not configured")
                sequence = (self.clock.latest[self.node_id][0].sequence + 1
                            if self.node_id in self.clock.latest else 1)
                now = time.time_ns() // 1_000_000
                signed = sign_heartbeat(
                    self.private_key, voter=self.node_id, epoch_digest=self.votes.epoch_digest,
                    sequence=sequence, round_number=self.ledger.round,
                    ledger_digest=protocol_digest("splitchain/sandbox-ledger/v1",
                                                  self.ledger.snapshot()),
                    sent_at_ms=now,
                )
                trial = ClockTracker.from_snapshot(self.votes, self.clock.snapshot())
                trial.observe(signed, now)
                self.store.save(self.ledger, self.votes, self.position, self.bets, trial)
                self.clock = trial
                return asdict(signed)
            if method in {"bet.commit", "bet.reveal_suffix"} and request.get("round") != self.ledger.round:
                raise ProtocolError("bet round differs from the locally verified ledger")
            if method == "bet.commit":
                bet = BetCommit(**request["event"])
                trial = TimestampBetBook.from_snapshot(self.votes, self.bets.snapshot())
                trial.commit(bet, request["round"])
                self.store.save(self.ledger, self.votes, self.position, trial, self.clock)
                self.bets = trial
                return {"stored": True, "commitment": bet.commitment}
            if method == "bet.reveal_suffix":
                suffix = BetSuffixReveal(**request["event"])
                trial = TimestampBetBook.from_snapshot(self.votes, self.bets.snapshot())
                trial.reveal_suffix(suffix, request["round"])
                self.store.save(self.ledger, self.votes, self.position, trial, self.clock)
                self.bets = trial
                return {"verified": True, "start": suffix.start_index,
                        "end": suffix.end_index}
            if method == "health":
                return {"epoch_digest": self.votes.epoch_digest,
                        "position": self.position, "round": self.ledger.round,
                        "commit_count": len(self.bets.commits)}
            if method == "round.challenge":
                return asdict(self.round_challenge())
            raise ProtocolError("unknown sandbox operation")

    async def handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        try:
            line = await reader.readline()
            if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
                raise ProtocolError("sandbox request is too large or incomplete")
            request = json.loads(line)
            response = {"result": await self.dispatch(request)}
        except (ProtocolError, ValueError, TypeError, KeyError) as exc:
            response = {"error": str(exc)}
        writer.write(json.dumps(response, sort_keys=True).encode() + b"\n")
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def serve(self) -> None:
        self.socket_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.socket_path.exists():
            raise ProtocolError("sandbox socket path already exists")
        server = await asyncio.start_unix_server(self.handle, str(self.socket_path),
                                                 limit=MAX_REQUEST + 1)
        os.chmod(self.socket_path, 0o600)
        try:
            async with server:
                await server.serve_forever()
        finally:
            self.socket_path.unlink(missing_ok=True)


async def sandbox_request(socket_path: str | Path, method: str,
                          event: BetCommit | BetSuffixReveal | StakeVote | StakeCertificate
                          | RoundHeartbeat | None = None,
                          round_number: int | None = None,
                          slots: tuple[tuple[str, int], ...] | None = None) -> dict:
    reader, writer = await asyncio.open_unix_connection(str(socket_path), limit=MAX_REQUEST + 1)
    try:
        request = {"method": method}
        if event is not None:
            request["event"] = asdict(event)
        if round_number is not None:
            request["round"] = round_number
        if slots is not None:
            request["slots"] = slots
        writer.write(json.dumps(request, sort_keys=True).encode() + b"\n")
        await writer.drain()
        line = await reader.readline()
        if len(line) > MAX_REQUEST or not line.endswith(b"\n"):
            raise ProtocolError("invalid sandbox response")
        response = json.loads(line)
        if "error" in response:
            raise ProtocolError(response["error"])
        return response["result"]
    finally:
        writer.close()
        await writer.wait_closed()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an isolated validator consensus sandbox")
    parser.add_argument("--socket", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--genesis", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--expected-epoch-digest", required=True)
    parser.add_argument("--node-id", help="Local validator ID for signed heartbeats")
    parser.add_argument("--node-key", help="Local Ed25519 private-key PEM file")
    args = parser.parse_args()
    genesis = GenesisConfig.from_dict(json.loads(Path(args.genesis).read_text()))
    membership, keys = load_manifest(args.manifest, genesis, args.expected_epoch_digest)
    store = SandboxConsensusStore(args.state, genesis=genesis,
                                  membership=membership, keys=keys)
    private = None
    if args.node_key:
        private = serialization.load_pem_private_key(Path(args.node_key).read_bytes(), None)
        if not isinstance(private, Ed25519PrivateKey):
            raise ProtocolError("sandbox heartbeat key must be Ed25519")
    asyncio.run(SandboxDaemon(args.socket, store, args.node_id, private).serve())


if __name__ == "__main__":
    main()
