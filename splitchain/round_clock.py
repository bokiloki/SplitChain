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

BACKENDS = {role: f"ws://{role}:8765" for role in ("primary", "secondary", "tertiary")}
ROUND_INTERVAL = 10
# Replica position, prepare and commit can each take up to three seconds.
ADVANCE_TIMEOUT = 15


async def leadership_view(backend: str) -> tuple[int, str] | None:
    try:
        async with asyncio.timeout(2):
            async with websockets.connect(backend, max_size=64 * 1024) as socket:
                await socket.send(json.dumps({"id": "round-leader", "method": "cluster.leadership"}))
                view = json.loads(await socket.recv())["result"]
                term, leader = view["term"], view["leader"]
                if type(term) is int and term >= 0 and leader in BACKENDS:
                    return term, leader
    except (OSError, TimeoutError, ValueError, KeyError, TypeError, websockets.WebSocketException):
        pass
    return None


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
        responses = await asyncio.gather(*(leadership_view(url) for url in BACKENDS.values()))
        views = {view for view in responses if view is not None}
        newest_term = max((term for term, _ in views), default=-1)
        leaders = {leader for term, leader in views if term == newest_term}
        if len(leaders) != 1:
            return {"error": {"code": "UNAVAILABLE", "message": "no unambiguous leader reported"}}
        leader = leaders.pop()
        request = self.next_request()
        sent = False
        try:
            async with asyncio.timeout(ADVANCE_TIMEOUT):
                async with websockets.connect(BACKENDS[leader], max_size=64 * 1024) as socket:
                    # Once sending begins, a dropped connection can hide a committed round.
                    sent = True
                    await socket.send(json.dumps(request))
                    result = json.loads(await socket.recv())
                    if not isinstance(result, dict) or result.get("id") != request["id"]:
                        raise ValueError("invalid advance response")
                    return result
        except (OSError, TimeoutError, ValueError, websockets.WebSocketException):
            if sent:
                return {"error": {"code": "COMMIT_UNCERTAIN", "message": f"{leader} did not confirm the round"}}
            return {"error": {"code": "UNAVAILABLE", "message": f"could not connect to {leader}"}}


async def main() -> None:
    secrets_file = Path(os.environ["TESTNET_AUTH_FILE"])
    accounts = json.loads(secrets_file.read_text(encoding="utf-8"))
    clock = RoundClock(accounts["testnet_operator"], Path("/var/lib/splitchain/round-nonce"))
    loop = asyncio.get_running_loop()
    next_start = loop.time()
    while True:
        result = await clock.step()
        if "error" in result:
            error = result["error"]
            print(f"round advance {error['code']}: {error['message']}", flush=True)
        next_start += ROUND_INTERVAL
        while next_start <= loop.time():
            next_start += ROUND_INTERVAL
        await asyncio.sleep(next_start - loop.time())


if __name__ == "__main__":
    asyncio.run(main())
