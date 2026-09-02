"""Atomic durable storage for the experimental reference node."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .model import Ledger, ProtocolError


class LedgerStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, default_balances: dict[str, int]) -> Ledger:
        ledger, _ = self.load_node_state(default_balances)
        return ledger

    def load_node_state(self, default_balances: dict[str, int]) -> tuple[Ledger, dict[str, int]]:
        ledger, replay, _ = self.load_full_node_state(default_balances)
        return ledger, replay

    def load_full_node_state(
        self, default_balances: dict[str, int]
    ) -> tuple[Ledger, dict[str, int], dict[str, int]]:
        if not self.path.exists():
            return Ledger(default_balances), {}, {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError("unable to load ledger state") from exc
        if data.get("schema") == "splitchain-node-state/v1":
            replay = data.get("replay_nonces", {})
            if not isinstance(replay, dict):
                raise ProtocolError("invalid replay state")
            replication = data.get("replication_nonces", {})
            if not isinstance(replication, dict):
                raise ProtocolError("invalid replication replay state")
            return Ledger.from_snapshot(data["ledger"]), {
                str(actor): int(nonce) for actor, nonce in replay.items()
            }, {str(node): int(nonce) for node, nonce in replication.items()}
        return Ledger.from_snapshot(data), {}, {}

    def save(
        self,
        ledger: Ledger,
        replay_nonces: dict[str, int] | None = None,
        replication_nonces: dict[str, int] | None = None,
        replication_log: list[dict] | None = None,
        replication_pending: dict | None = None,
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        document = {
            "schema": "splitchain-node-state/v1",
            "ledger": ledger.snapshot(),
            "replay_nonces": dict(sorted((replay_nonces or {}).items())),
            "replication_nonces": dict(sorted((replication_nonces or {}).items())),
            "replication_log": replication_log or [],
            "replication_pending": replication_pending,
        }
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ProtocolError("unable to persist ledger state") from exc

    def load_replication_log(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            value = data.get("replication_log", [])
            if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
                raise ProtocolError("invalid replication log")
            return value
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError("unable to load replication log") from exc

    def load_replication_pending(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8")).get(
                "replication_pending"
            )
            if value is not None and not isinstance(value, dict):
                raise ProtocolError("invalid pending replication record")
            return value
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError("unable to load pending replication record") from exc
