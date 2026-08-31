"""scplit command-line client and local simulator frontend."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from dataclasses import asdict

from .simulator import run


async def rpc(url: str, method: str, params: dict) -> dict:
    import websockets

    request = {"id": uuid.uuid4().hex[:8], "method": method, "params": params}
    async with websockets.connect(url, max_size=64 * 1024) as socket:
        await socket.send(json.dumps(request))
        return json.loads(await socket.recv())


def main() -> None:
    parser = argparse.ArgumentParser(prog="scplit", description="SplitChain research CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sim = sub.add_parser("simulate", help="run the seeded invariant simulator")
    sim.add_argument("--seed", type=int, default=1)
    sim.add_argument("--steps", type=int, default=200)
    sim.add_argument("--accounts", type=int, default=8)

    call = sub.add_parser("rpc", help="call a local splitd node")
    call.add_argument("method", choices=("status", "offer", "accept", "commit", "cancel", "advance"))
    call.add_argument("--params", default="{}", help="JSON object")
    call.add_argument("--url", default="ws://127.0.0.1:8765")

    args = parser.parse_args()
    if args.command == "simulate":
        print(json.dumps(asdict(run(args.seed, args.steps, args.accounts)), indent=2))
    else:
        params = json.loads(args.params)
        if not isinstance(params, dict):
            parser.error("--params must be a JSON object")
        print(json.dumps(asyncio.run(rpc(args.url, args.method, params)), indent=2))


if __name__ == "__main__":
    main()

