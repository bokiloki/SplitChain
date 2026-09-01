"""Authenticated RPC envelopes with deterministic replay protection."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from .model import ProtocolError, canonical_json


class RequestAuthenticator:
    def __init__(self, secrets: dict[str, str]) -> None:
        self._secrets = {actor: secret.encode() for actor, secret in secrets.items()}
        self._nonces: dict[str, int] = {}

    @staticmethod
    def message(request: dict[str, Any], actor: str, nonce: int) -> bytes:
        return canonical_json({
            "actor": actor,
            "id": request.get("id"),
            "method": request.get("method"),
            "nonce": nonce,
            "params": request.get("params", {}),
        })

    @classmethod
    def sign(cls, request: dict[str, Any], actor: str, nonce: int, secret: str) -> dict:
        signature = hmac.new(
            secret.encode(), cls.message(request, actor, nonce), hashlib.sha256
        ).hexdigest()
        return {"actor": actor, "nonce": nonce, "signature": signature}

    def verify(self, request: dict[str, Any]) -> str:
        auth = request.get("auth")
        if not isinstance(auth, dict):
            raise ProtocolError("authenticated request required")
        try:
            actor = str(auth["actor"])
            nonce = int(auth["nonce"])
            signature = str(auth["signature"])
            secret = self._secrets[actor]
        except (KeyError, TypeError, ValueError) as exc:
            raise ProtocolError("invalid request authentication") from exc
        if nonce <= self._nonces.get(actor, -1):
            raise ProtocolError("request nonce was already used")
        expected = hmac.new(
            secret, self.message(request, actor, nonce), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ProtocolError("invalid request signature")
        self._nonces[actor] = nonce
        return actor
