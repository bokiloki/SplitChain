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
        if not self.path.exists():
            return Ledger(default_balances)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError("unable to load ledger state") from exc
        return Ledger.from_snapshot(data)

    def save(self, ledger: Ledger) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        payload = json.dumps(ledger.snapshot(), sort_keys=True, separators=(",", ":"))
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
