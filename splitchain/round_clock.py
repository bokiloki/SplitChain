"""Operator-owned round driver for the valueless single-host sandbox."""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from pathlib import Path

import websockets

from .auth import RequestAuthenticator

BACKENDS = ("ws://primary:8765", "ws://secondary:8765", "ws://tertiary:8765")


class RoundClock:
    def __init__(self, secret: str, state: Path) -> None:
        if len(secret) < 32:
            raise ValueError("operator secret is too short")
        self.secret = secret
        self.state = state
        self.last_nonce = int(state.read_text()) if state.exists() else -1
        if self.last_nonce < -1:
            raise ValueError("invalid round clock nonce")

    def next_request(self) -> dict:
        nonce = max(time.time_ns(), self.last_nonce + 1)
        self.state.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(str(nonce))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.state)
        directory = os.open(self.state.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        self.last_nonce = nonce
        request = {"id": uuid.uuid4().hex[:12], "method": "advance", "params": {"rounds": 1}}
        request["auth"] = RequestAuthenticator.sign(
            request, "testnet_operator", nonce, self.secret)
        return request

    async def step(self) -> dict:
        request = self.next_request()
        for backend in BACKENDS:
            try:
                async with asyncio.timeout(4):
                    async with websockets.connect(backend, max_size=64 * 1024) as socket:
                        await socket.send(json.dumps(request))
                        result = json.loads(await socket.recv())
                        if result.get("error", {}).get("code") == "NOT_LEADER":
                            continue
                        return result
            except (OSError, TimeoutError, ValueError, websockets.WebSocketException):
                continue
        return {"error": {"code": "UNAVAILABLE", "message": "round did not reach a leader"}}


async def main() -> None:
    secrets_file = Path(os.environ["TESTNET_AUTH_FILE"])
    accounts = json.loads(secrets_file.read_text(encoding="utf-8"))
    clock = RoundClock(accounts["testnet_operator"], Path("/var/lib/splitchain/round-nonce"))
    while True:
        result = await clock.step()
        if "error" in result:
            print(f"round rejected: {result['error']['code']}", flush=True)
        await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
