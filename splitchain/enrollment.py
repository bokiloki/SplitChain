"""Private, operator-approved enrollment for the valueless public testnet."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import stat
import time
import uuid
from pathlib import Path

import websockets

from .auth import RequestAuthenticator
from .distributor import BACKENDS, atomic_json, read_json
from .model import GenesisConfig


class EnrollmentError(ValueError):
    pass


def request_from_file(path: Path, genesis: GenesisConfig) -> tuple[str, str]:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) or path.stat().st_mode & 0o077:
        raise EnrollmentError("enrollment request must be a private regular file (mode 0600)")
    request = read_json(path)
    required = {"schema", "network_id", "account", "credential"}
    if set(request) not in (required, required | {"receipt"}) or (
        request["schema"] != "splitchain-wallet-enrollment/v1"
        or request["network_id"] != genesis.network_id
    ):
        raise EnrollmentError("enrollment request does not match this testnet")
    if "receipt" in request and (not isinstance(request["receipt"], str)
                                 or not re.fullmatch(r"[0-9a-f]{48}", request["receipt"])):
        raise EnrollmentError("invalid enrollment receipt")
    account, secret = request["account"], request["credential"]
    if (not isinstance(account, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,31}", account)
            or account.startswith("testnet_") or account in genesis.allocations
            or not isinstance(secret, str) or not re.fullmatch(r"[0-9a-f]{64}", secret)):
        raise EnrollmentError("invalid wallet ID or credential")
    return account, secret


def approve(request: Path, registry: Path, genesis: GenesisConfig) -> str:
    account, secret = request_from_file(request, genesis)
    if registry.is_symlink() or not stat.S_ISREG(registry.stat().st_mode) or registry.stat().st_mode & 0o077:
        raise EnrollmentError("registry must be a private regular file")
    original = registry.stat()
    stored = read_json(registry)
    if account in stored:
        if stored[account] != secret:
            raise EnrollmentError("wallet ID already enrolled with a different credential")
        return account
    if "testnet_operator" not in stored:
        raise EnrollmentError("operator credential missing")
    # Recheck before replacing; never silently overwrite an existing actor.
    fresh = read_json(registry)
    if fresh != stored or registry.stat().st_ino != original.st_ino:
        raise EnrollmentError("registry changed during enrollment; retry")
    atomic_json(registry, {**stored, account: secret}, owner=original)
    return account


async def activate(account: str, registry: Path, genesis: GenesisConfig) -> dict:
    stored = read_json(registry)
    if account not in stored or "testnet_operator" not in stored:
        raise EnrollmentError("approve the wallet in the registry first")
    for backend in BACKENDS:
        try:
            async with asyncio.timeout(5):
                async with websockets.connect(backend, max_size=64 * 1024) as ws:
                    await ws.send(json.dumps({"id": "enrollment-status", "method": "status", "params": {}}))
                    status = json.loads(await ws.recv())
            ledger = status["result"]
            if ledger.get("genesis") != genesis.public():
                raise EnrollmentError("connected node has a different genesis")
            if account in ledger["balances"]:
                return {"account": account, "status": "already active"}
            request = {"id": uuid.uuid4().hex[:12], "method": "account.register", "params": {"account": account}}
            request["auth"] = RequestAuthenticator.sign(
                request, "testnet_operator", time.time_ns(), stored["testnet_operator"])
            async with asyncio.timeout(7):
                async with websockets.connect(backend, max_size=64 * 1024) as ws:
                    await ws.send(json.dumps(request))
                    response = json.loads(await ws.recv())
            if response.get("id") != request["id"]:
                raise EnrollmentError("unexpected RPC response; inspect ledger before retrying")
            if response.get("error", {}).get("code") == "NOT_LEADER":
                continue
            if "error" in response:
                raise EnrollmentError(f"registration rejected: {response['error']['code']} ({response['error']['message']})")
            return {"account": account, "status": "active", "result": response["result"]}
        except (OSError, TimeoutError, websockets.WebSocketException) as exc:
            raise EnrollmentError("RPC result unknown; inspect status before retrying") from exc
    raise EnrollmentError("no leader accepted registration; check cluster leadership")


def main() -> None:
    parser = argparse.ArgumentParser(description="Approve and activate private wallet enrollment requests")
    parser.add_argument("command", choices=("approve", "activate"))
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--registry", type=Path, default=Path("/operator/auth/accounts.json"))
    parser.add_argument("--genesis", type=Path, default=Path("/etc/splitchain/testnet-genesis.json"))
    args = parser.parse_args()
    try:
        genesis = GenesisConfig.from_dict(read_json(args.genesis))
        account, secret = request_from_file(args.request, genesis)
        if args.command == "approve":
            approve(args.request, args.registry, genesis)
            print(f"Approved {account} in private registry. Recreate nodes, then activate; credential not printed.")
        else:
            if read_json(args.registry).get(account) != secret:
                raise EnrollmentError("request credential does not match approved registry")
            print(json.dumps(asyncio.run(activate(account, args.registry, genesis))))
    except (EnrollmentError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"enrollment stopped: {exc}\n")


if __name__ == "__main__":
    main()
