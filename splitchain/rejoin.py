"""Durable catch-up records kept separate from a validator's live event path."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .model import ProtocolError, protocol_digest


@dataclass(frozen=True)
class RejoinCheckpoint:
    epoch_digest: str
    position: int
    round: int
    ledger_digest: str
    state_digest: str


@dataclass(frozen=True)
class RejoinEnvelope:
    sequence: int
    parent_digest: str
    message: dict
    digest: str

    @classmethod
    def create(cls, sequence: int, parent_digest: str, message: dict) -> RejoinEnvelope:
        if type(sequence) is not int or sequence < 1 or not parent_digest or not isinstance(message, dict):
            raise ProtocolError("invalid rejoin event")
        digest = protocol_digest("splitchain/rejoin-event/v1", {
            "sequence": sequence, "parent_digest": parent_digest, "message": message,
        })
        return cls(sequence, parent_digest, message, digest)

    def verify(self, expected_sequence: int, expected_parent: str) -> None:
        if self.sequence != expected_sequence or self.parent_digest != expected_parent:
            raise ProtocolError("rejoin event is out of order")
        expected = protocol_digest("splitchain/rejoin-event/v1", {
            "sequence": self.sequence, "parent_digest": self.parent_digest,
            "message": self.message,
        })
        if self.digest != expected:
            raise ProtocolError("rejoin event digest mismatch")


class RejoinQueue:
    """Atomic durable queue for events received while a node catches up."""

    def __init__(self, path: str | Path, checkpoint: RejoinCheckpoint) -> None:
        self.path = Path(path)
        self.checkpoint = checkpoint
        self.events: list[RejoinEnvelope] = []
        if self.path.exists():
            self._load()

    @property
    def next_sequence(self) -> int:
        return len(self.events) + 1

    @property
    def parent_digest(self) -> str:
        return self.events[-1].digest if self.events else self.checkpoint.state_digest

    def append(self, message: dict) -> RejoinEnvelope:
        event = RejoinEnvelope.create(self.next_sequence, self.parent_digest, message)
        self.events.append(event)
        self._save()
        return event

    def append_envelope(self, event: RejoinEnvelope) -> None:
        event.verify(self.next_sequence, self.parent_digest)
        self.events.append(event)
        self._save()

    def drain(self, checkpoint: RejoinCheckpoint) -> list[dict]:
        if checkpoint != self.checkpoint:
            raise ProtocolError("rejoin checkpoint changed during catch-up")
        messages = [event.message for event in self.events]
        self.events.clear()
        self._save()
        return messages

    def snapshot(self) -> dict:
        return {"schema": "splitchain/rejoin-queue/v1", "checkpoint": asdict(self.checkpoint),
                "events": [asdict(event) for event in self.events]}

    def _save(self) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        document = self.snapshot()
        document["digest"] = protocol_digest("splitchain/rejoin-queue/v1", document)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    def _load(self) -> None:
        try:
            document = json.loads(self.path.read_text())
            digest = document.pop("digest")
            if digest != protocol_digest("splitchain/rejoin-queue/v1", document):
                raise ProtocolError("rejoin queue digest mismatch")
            if document["schema"] != "splitchain/rejoin-queue/v1":
                raise ProtocolError("invalid rejoin queue")
            if RejoinCheckpoint(**document["checkpoint"]) != self.checkpoint:
                raise ProtocolError("rejoin queue checkpoint mismatch")
            parent = self.checkpoint.state_digest
            for raw in document["events"]:
                event = RejoinEnvelope(**raw)
                event.verify(len(self.events) + 1, parent)
                self.events.append(event)
                parent = event.digest
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProtocolError("invalid rejoin queue") from exc
