"""Per-validator durable sandbox state for an offline stake-consensus rehearsal."""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .clock_heartbeat import ClockTracker
from .membership import StakeMembership
from .model import GenesisConfig, Ledger, ProtocolError, protocol_digest
from .stake_votes import StakeVoteBook
from .timestamp_bets import TimestampBetBook


class SandboxConsensusStore:
    """Persist independently checked copies; callers must protect the directory."""

    def __init__(self, path: str | Path, *, genesis: GenesisConfig,
                 membership: StakeMembership, keys: dict[str, Ed25519PublicKey]) -> None:
        self.path = Path(path)
        self.genesis = genesis
        self.membership = membership
        self.keys = keys.copy()

    def _book(self) -> StakeVoteBook:
        return StakeVoteBook(
            self.membership, self.genesis.network_id, self.genesis.digest(), self.keys,
        )

    def save(self, ledger: Ledger, book: StakeVoteBook, position: int,
             bets: TimestampBetBook | None = None,
             clock: ClockTracker | None = None) -> None:
        if type(position) is not int or position < 0 or ledger.genesis != self.genesis:
            raise ProtocolError("sandbox state does not match genesis or log position")
        ledger._validate_restored_state()
        expected = self._book()
        if book.epoch_digest != expected.epoch_digest:
            raise ProtocolError("sandbox vote book is for another stake epoch")
        verified = StakeVoteBook.from_snapshot(
            self.membership, self.genesis.network_id, self.genesis.digest(),
            self.keys, book.snapshot(),
        )
        if bets is not None and bets.votes.epoch_digest != verified.epoch_digest:
            raise ProtocolError("sandbox bets are for another stake epoch")
        checked_bets = TimestampBetBook.from_snapshot(verified, bets.snapshot()) if bets else None
        if clock is not None and clock.book.epoch_digest != verified.epoch_digest:
            raise ProtocolError("sandbox heartbeat tracker is for another stake epoch")
        checked_clock = ClockTracker.from_snapshot(verified, clock.snapshot()) if clock else None
        document = {
            "schema": "splitchain/sandbox-consensus/v1",
            "genesis_digest": self.genesis.digest(),
            "epoch_digest": verified.epoch_digest,
            "position": position,
            "ledger": ledger.snapshot(),
            "votes": verified.snapshot(),
            "bets": checked_bets.snapshot() if checked_bets else TimestampBetBook(verified).snapshot(),
            "heartbeats": checked_clock.snapshot() if checked_clock else ClockTracker(verified).snapshot(),
        }
        document["digest"] = protocol_digest("splitchain/sandbox-state/v1", document)
        parent = self.path.parent
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if parent.stat().st_mode & 0o077:
            raise ProtocolError("sandbox consensus directory must be private")
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise

    def load(self) -> tuple[Ledger, StakeVoteBook, int, TimestampBetBook, ClockTracker]:
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            expected_digest = document.pop("digest")
            if (
                document["schema"] != "splitchain/sandbox-consensus/v1"
                or expected_digest != protocol_digest("splitchain/sandbox-state/v1", document)
                or document["genesis_digest"] != self.genesis.digest()
                or document["epoch_digest"] != self._book().epoch_digest
                or type(document["position"]) is not int or document["position"] < 0
            ):
                raise ProtocolError("sandbox consensus checkpoint is invalid")
            ledger = Ledger.from_snapshot(document["ledger"], expected_genesis=self.genesis)
            book = StakeVoteBook.from_snapshot(
                self.membership, self.genesis.network_id, self.genesis.digest(),
                self.keys, document["votes"],
            )
            bets = TimestampBetBook.from_snapshot(book, document["bets"])
            clock = ClockTracker.from_snapshot(book, document["heartbeats"])
            return ledger, book, document["position"], bets, clock
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("sandbox consensus checkpoint is invalid") from exc
