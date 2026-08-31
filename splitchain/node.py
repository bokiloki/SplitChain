"""Minimal JSON-over-WebSocket reference node for local experiments."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from .model import Ledger, ProtocolError


class ReferenceNode:
    def __init__(self, balances: dict[str, int] | None = None) -> None:
        self.ledger = Ledger(balances or {"alice": 1_000, "bob": 1_000})
        self._lock = asyncio.Lock()

    async def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})
        try:
            async with self._lock:
                if method == "status":
                    result = self.ledger.snapshot()
                elif method == "offer":
                    result = self.ledger.offer(**params).public()
                elif method == "accept":
                    result = self.ledger.accept(**params).public()
                elif method == "commit":
                    result = self.ledger.commit(**params).public()
                elif method == "cancel":
                    result = self.ledger.cancel(**params).public()
                elif method == "advance":
                    result = [b.public() for b in self.ledger.advance(**params)]
                else:
                    raise ProtocolError("unknown method")
            return {"id": request_id, "result": result}
        except (ProtocolError, TypeError) as exc:
            return {"id": request_id, "error": {"code": "INVALID_REQUEST", "message": str(exc)}}

    async def handler(self, websocket: Any) -> None:
        async for raw in websocket:
            try:
                request = json.loads(raw)
                if not isinstance(request, dict):
                    raise TypeError("request must be an object")
                response = await self.dispatch(request)
            except (json.JSONDecodeError, TypeError) as exc:
                response = {"id": None, "error": {"code": "INVALID_JSON", "message": str(exc)}}
            await websocket.send(json.dumps(response, sort_keys=True))


async def serve(host: str, port: int) -> None:
    import websockets

    node = ReferenceNode()
    async with websockets.serve(node.handler, host, port, max_size=64 * 1024):
        print(f"splitd listening on ws://{host}:{port}")
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="SplitChain experimental reference node")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    asyncio.run(serve(args.host, args.port))


if __name__ == "__main__":
    main()
