"""Atomic durable storage for the experimental reference node."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .model import GenesisConfig, Ledger, ProtocolError


class LedgerStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.heartbeat_path = self.path.with_name(self.path.name + ".heartbeat")
        self._heartbeat_anchor: str | None = None

    @staticmethod
    def _anchor(document: dict) -> str:
        # Bind heartbeat-only updates to the durable checkpoint, without hashing
        # or rewriting the growing replication history on every heartbeat.
        leadership = dict(document.get("leadership") or {})
        leadership.pop("last_heartbeat_tick", None)
        value = {key: document.get(key) for key in (
            "schema", "ledger", "replay_nonces", "replication_nonces", "replication_pending",
        )}
        value["leadership"] = leadership
        return hashlib.sha256(json.dumps(value, sort_keys=True,
                                         separators=(",", ":")).encode()).hexdigest()

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ProtocolError("unable to persist node state") from exc

    def save_heartbeat(self, leadership: dict) -> None:
        if self._heartbeat_anchor is None:
            raise ProtocolError("heartbeat requires a durable checkpoint")
        tick = leadership.get("last_heartbeat_tick")
        if type(tick) is not int or tick < 0:
            raise ProtocolError("invalid heartbeat tick")
        self._atomic_write(self.heartbeat_path, json.dumps({
            "schema": "splitchain-heartbeat/v1", "anchor": self._heartbeat_anchor,
            "last_heartbeat_tick": tick,
        }, sort_keys=True, separators=(",", ":")))

    def load(self, default_balances: dict[str, int]) -> Ledger:
        ledger, _ = self.load_node_state(default_balances)
        return ledger

    def load_node_state(self, default_balances: dict[str, int]) -> tuple[Ledger, dict[str, int]]:
        ledger, replay, _ = self.load_full_node_state(default_balances)
        return ledger, replay

    def load_full_node_state(
        self, default_balances: dict[str, int], genesis: GenesisConfig | None = None
    ) -> tuple[Ledger, dict[str, int], dict[str, int]]:
        if not self.path.exists():
            return Ledger(genesis=genesis) if genesis else Ledger(default_balances), {}, {}
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
            return Ledger.from_snapshot(data["ledger"], expected_genesis=genesis), {
                str(actor): int(nonce) for actor, nonce in replay.items()
            }, {str(node): int(nonce) for node, nonce in replication.items()}
        return Ledger.from_snapshot(data, expected_genesis=genesis), {}, {}

    def save(
        self,
        ledger: Ledger,
        replay_nonces: dict[str, int] | None = None,
        replication_nonces: dict[str, int] | None = None,
        replication_log: list[dict] | None = None,
        replication_pending: dict | None = None,
        leadership: dict | None = None,
    ) -> None:
        document = {
            "schema": "splitchain-node-state/v1",
            "ledger": ledger.snapshot(),
            "replay_nonces": dict(sorted((replay_nonces or {}).items())),
            "replication_nonces": dict(sorted((replication_nonces or {}).items())),
            "replication_log": replication_log or [],
            "replication_pending": replication_pending,
            "leadership": leadership,
        }
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        self._atomic_write(self.path, payload)
        self._heartbeat_anchor = self._anchor(document)

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

    def load_leadership_snapshot(self) -> dict | None:
        if not self.path.exists():
            return None
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
            value = document.get("leadership")
            if value is not None and not isinstance(value, dict):
                raise ProtocolError("invalid leadership record")
            self._heartbeat_anchor = self._anchor(document) if value is not None else None
            if value is not None and self.heartbeat_path.exists():
                heartbeat = json.loads(self.heartbeat_path.read_text(encoding="utf-8"))
                if (not isinstance(heartbeat, dict)
                        or set(heartbeat) != {"schema", "anchor", "last_heartbeat_tick"}
                        or heartbeat["schema"] != "splitchain-heartbeat/v1"
                        or not isinstance(heartbeat["anchor"], str)
                        or type(heartbeat["last_heartbeat_tick"]) is not int
                        or heartbeat["last_heartbeat_tick"] < 0):
                    raise ProtocolError("invalid durable heartbeat")
                if heartbeat["anchor"] == self._heartbeat_anchor:
                    value["last_heartbeat_tick"] = max(
                        value["last_heartbeat_tick"], heartbeat["last_heartbeat_tick"]
                    )
            return value
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError("unable to load leadership record") from exc
