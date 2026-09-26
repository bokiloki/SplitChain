"""Restricted WebSocket ingress for authenticated sandbox transfers."""

from __future__ import annotations

import asyncio
import json
import os
import time

import websockets

BACKENDS = ("ws://primary:8765", "ws://secondary:8765", "ws://tertiary:8765")
PUBLIC_METHODS = frozenset({"offer", "accept", "commit", "cancel"})


class Gateway:
    def __init__(self) -> None:
        self.capacity = asyncio.Semaphore(32)
        self.requests_by_ip: dict[str, tuple[float, int]] = {}

    async def handler(self, socket: websockets.ServerConnection) -> None:
        if socket.request.path != "/rpc":
            await socket.close(code=1008, reason="unknown endpoint")
            return
        async with self.capacity:
            try:
                async with asyncio.timeout(10):
                    raw = await socket.recv()
                    request = json.loads(raw)
                    if not isinstance(request, dict) or request.get("method") not in PUBLIC_METHODS:
                        raise ValueError("only signed transfer methods are public")
                    if not isinstance(request.get("params"), dict) or not isinstance(request.get("auth"), dict):
                        raise TypeError("signed account request required")
                    peer = socket.remote_address
                    client_ip = peer[0] if peer else "unknown"
                    now = time.monotonic()
                    started, count = self.requests_by_ip.get(client_ip, (now, 0))
                    if now - started >= 60:
                        started, count = now, 0
                    self.requests_by_ip[client_ip] = (started, count + 1)
                    if count + 1 > 120:
                        raise ValueError("gateway request limit reached")
                    if len(self.requests_by_ip) > 1024:
                        self.requests_by_ip = {
                            ip: window for ip, window in self.requests_by_ip.items()
                            if now - window[0] < 60
                        }
                    response = await self.forward(request)
            except (TypeError, ValueError, TimeoutError) as exc:
                response = {"id": None, "error": {"code": "INVALID_REQUEST", "message": str(exc)}}
            await socket.send(json.dumps(response, sort_keys=True))

    async def forward(self, request: dict) -> dict:
        for backend in BACKENDS:
            try:
                async with asyncio.timeout(3):
                    async with websockets.connect(backend, max_size=64 * 1024) as internal:
                        await internal.send(json.dumps(request))
                        response = json.loads(await internal.recv())
                        if response.get("error", {}).get("code") == "NOT_LEADER":
                            continue
                        return response
            except (OSError, TimeoutError, ValueError, websockets.WebSocketException):
                continue
        return {"id": request.get("id"), "error": {
            "code": "UNAVAILABLE", "message": "no certified leader accepted the request",
        }}


async def main() -> None:
    gateway = Gateway()
    port = int(os.environ.get("PUBLIC_RPC_PORT", "8089"))
    async with websockets.serve(gateway.handler, "0.0.0.0", port, max_size=8192,
                                max_queue=8, open_timeout=5):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
