"""Operator-only batch funding for 100 newly provisioned, valueless testnet wallets.

Run this in the one-off `distributor` Compose service, never in a public node.
The operator holds newly generated recipients' credentials until delivery and
signs their acceptance for this initial funding batch.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import os
import re
import secrets
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import websockets

from .auth import RequestAuthenticator
from .model import GenesisConfig, canonical_json

BACKENDS = ("ws://primary:8765", "ws://secondary:8765", "ws://tertiary:8765")
SCHEMA = "splitchain-friend-distribution/v1"
SENDER = "testnet_faucet"
SMS_URL = "https://bokiloki.ddns.net/splitchain/downloads"
ACCOUNT_PATTERN = re.compile(r"[a-z][a-z0-9_]{2,31}\Z")


class DistributionError(ValueError):
    pass


def private_directory(path: Path) -> None:
    if path.is_symlink():
        raise DistributionError("distribution directory must not be a symlink")
    if not path.exists():
        path.mkdir(mode=0o700)
    mode = path.stat().st_mode
    if not stat.S_ISDIR(mode) or mode & 0o077:
        raise DistributionError(f"directory must be private (mode 0700): {path}")


def atomic_json(path: Path, value: dict, *, owner: os.stat_result | None = None) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if owner is not None:
            os.fchown(fd, owner.st_uid, owner.st_gid)
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical_json(value) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise DistributionError(f"expected JSON object: {path}")
    return value


def accounts_for(prefix: str, count: int) -> list[str]:
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,24}", prefix) or prefix.startswith("testnet_"):
        raise DistributionError("prefix must start with a lowercase letter and contain only lowercase letters, digits or underscore")
    if not 1 <= count <= 100:
        raise DistributionError("count must be between 1 and 100")
    accounts = [f"{prefix}{i:03d}" for i in range(1, count + 1)]
    if any(not ACCOUNT_PATTERN.fullmatch(actor) for actor in accounts):
        raise DistributionError("generated account ID exceeds 32 characters")
    return accounts


def plan_for(prefix: str, count: int, amount: int, genesis: GenesisConfig) -> dict:
    if amount < 1 or count * amount > genesis.allocations[SENDER]:
        raise DistributionError("amount must be positive and total cannot exceed the faucet allocation")
    return {"schema": SCHEMA, "network_id": genesis.network_id,
            "genesis_digest": genesis.digest(), "sender": SENDER,
            "recipients": accounts_for(prefix, count), "amount": amount, "total": count * amount}


@contextmanager
def exclusive(output: Path):
    private_directory(output)
    fd = os.open(output / ".distribution.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def draft(actor: str, secret: str, amount: int) -> str:
    return (f"SplitChain TESTNET (valueless) - {amount} test units\n"
            f"Wallet ID: {actor}\nCredential: {secret}\n"
            f"Wallet app or browser: {SMS_URL}\n"
            "Keep the credential private. These are sandbox units, not real coins.\n")


def credential(path: Path, actor: str) -> str:
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise DistributionError(f"credential file must be private: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 3 or lines[1] != f"Wallet ID: {actor}" or not lines[2].startswith("Credential: "):
        raise DistributionError(f"invalid SMS draft for {actor}")
    secret = lines[2].removeprefix("Credential: ")
    if not re.fullmatch(r"[0-9a-f]{64}", secret):
        raise DistributionError(f"invalid credential for {actor}")
    return secret


def prepare(registry: Path, output: Path, plan: dict) -> None:
    with exclusive(output):
        original = registry.stat()
        if original.st_mode & 0o077:
            raise DistributionError("account registry must not be readable by other users")
        stored = read_json(registry)
        if SENDER not in stored:
            raise DistributionError("faucet account missing from registry")
        plan_path = output / "plan.json"
        if plan_path.exists():
            if read_json(plan_path) != plan:
                raise DistributionError("existing plan differs; use a separate directory for another batch")
        else:
            if any(a in stored for a in plan["recipients"]):
                raise DistributionError("one or more generated IDs already exist; change the prefix")
            if list(output.glob("*.sms.txt")):
                raise DistributionError("unrecognized SMS files in distribution directory")
            atomic_json(plan_path, plan)
        additions = {}
        for actor in plan["recipients"]:
            target = output / f"{actor}.sms.txt"
            if target.exists():
                secret = credential(target, actor)
            else:
                if actor in stored:
                    raise DistributionError(f"missing SMS draft for enrolled account {actor}; inspect before retrying")
                secret = secrets.token_hex(32)
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(draft(actor, secret, plan["amount"]))
                    handle.flush()
                    os.fsync(handle.fileno())
            if actor in stored and stored[actor] != secret:
                raise DistributionError(f"registry credential mismatch for {actor}")
            additions[actor] = secret
        if any(stored.get(k) != v for k, v in additions.items()):
            fresh = read_json(registry)
            if any(k in fresh and fresh[k] != v for k, v in additions.items()):
                raise DistributionError("registry changed during preparation")
            atomic_json(registry, {**fresh, **additions}, owner=original)
        print(f"Prepared {len(additions)} private SMS drafts in {output}; total {plan['total']} valueless units.")
        print("Recreate primary, secondary, tertiary and rounds containers before funding.")


class Distributor:
    def __init__(self, registry: Path, output: Path, genesis: GenesisConfig, *, rpc_interval: float = 0.25):
        self.registry = read_json(registry)
        self.genesis = genesis
        self.output = output
        self.plan = read_json(output / "plan.json")
        if self.plan.get("schema") != SCHEMA or self.plan.get("genesis_digest") != genesis.digest():
            raise DistributionError("plan differs from pinned genesis")
        if not 0 <= rpc_interval <= 5:
            raise DistributionError("RPC interval must be between zero and five seconds")
        self.rpc_interval = rpc_interval
        self.last_rpc = 0.0
        self.state_path = output / "state.json"
        self.state = read_json(self.state_path) if self.state_path.exists() else {
            "schema": SCHEMA, "plan_digest": self._plan_digest(), "last_nonces": {},
            "known_branches": {}, "in_flight": None}
        if self.state.get("schema") != SCHEMA or self.state.get("plan_digest") != self._plan_digest():
            raise DistributionError("state belongs to a different distribution plan")
        self.state.setdefault("known_branches", {})
        for actor in self.plan["recipients"]:
            if credential(output / f"{actor}.sms.txt", actor) != self.registry.get(actor):
                raise DistributionError(f"registry and SMS draft disagree for {actor}; recreate nodes after preparing")
        if not isinstance(self.registry.get(SENDER), str):
            raise DistributionError("faucet credential missing")

    def _plan_digest(self) -> str:
        import hashlib
        return hashlib.sha256(canonical_json(self.plan)).hexdigest()

    def save(self) -> None:
        atomic_json(self.state_path, self.state)

    async def call(self, request: dict) -> dict:
        now = time.monotonic()
        await asyncio.sleep(max(0, self.rpc_interval - (now - self.last_rpc)))
        self.last_rpc = time.monotonic()
        for backend in BACKENDS:
            try:
                async with asyncio.timeout(7):
                    async with websockets.connect(backend, max_size=64 * 1024) as ws:
                        await ws.send(json.dumps(request))
                        response = json.loads(await ws.recv())
                if response.get("id") != request["id"]:
                    raise DistributionError("RPC returned a mismatched request ID; inspect the ledger")
                if response.get("error", {}).get("code") == "NOT_LEADER":
                    continue
                return response
            except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                # A disconnected mutation may have been applied: do not retry it on another node.
                raise DistributionError("RPC outcome unknown; rerun to reconcile status") from exc
        raise DistributionError("no certified leader available; rerun after node recovery")

    async def snapshot(self) -> dict:
        for backend in BACKENDS:
            try:
                async with asyncio.timeout(6):
                    async with websockets.connect(backend, max_size=64 * 1024) as ws:
                        req = {"id": uuid.uuid4().hex[:12], "method": "status", "params": {}}
                        await ws.send(json.dumps(req))
                        response = json.loads(await ws.recv())
                ledger = response["result"]
                if ledger.get("genesis") != self.genesis.public():
                    raise DistributionError("connected node is on a different genesis")
                return ledger
            except (OSError, TimeoutError, websockets.WebSocketException, KeyError, TypeError, ValueError):
                continue
        raise DistributionError("no testnet node returned a valid ledger status")

    async def inspect_node(self, backend: str) -> dict:
        """Read only positions and digests; never print authentication material."""
        import hashlib

        name = backend.removeprefix("ws://").split(":")[0]
        result = {"node": name}
        for method, params in (("status", {}), ("cluster.leadership", {}),
                               ("replica.history", {"from": 0})):
            try:
                async with asyncio.timeout(4):
                    async with websockets.connect(backend, max_size=64 * 1024) as socket:
                        request = {"id": uuid.uuid4().hex[:12], "method": method,
                                   "params": params}
                        await socket.send(json.dumps(request))
                        response = json.loads(await socket.recv())
                if "error" in response:
                    result[method + "_error"] = response["error"].get("message", "RPC rejected")
                    continue
                value = response["result"]
                if method == "status":
                    result["height"] = value["canonical_head"]["height"]
                    result["round"] = value["round"]
                    result["ledger_digest"] = hashlib.sha256(canonical_json(value)).hexdigest()
                    result["genesis_matches"] = value.get("genesis") == self.genesis.public()
                elif method == "cluster.leadership":
                    result["leader"] = value["leader"]
                    result["term"] = value["term"]
                    result["leadership_nonce"] = value.get("committed_nonce")
                else:
                    result["replication_nonce"] = value["nonce"]
                    result["replication_digest"] = value["ledger_digest"]
            except (OSError, TimeoutError, websockets.WebSocketException,
                    ValueError, KeyError, TypeError) as exc:
                result[method + "_error"] = type(exc).__name__
        return result

    async def diagnose(self, *, attempts: int = 1) -> list[dict]:
        """Require a certified leader and one matching replica before any spending."""
        rows = []
        for attempt in range(attempts):
            rows = list(await asyncio.gather(*(self.inspect_node(url) for url in BACKENDS)))
            for leader in rows:
                if (leader.get("leader") != leader["node"] or
                        not leader.get("genesis_matches") or
                        leader.get("replication_digest") != leader.get("ledger_digest") or
                        leader.get("replication_nonce") is None):
                    continue
                for follower in rows:
                    if (follower["node"] == leader["node"] or
                            not follower.get("genesis_matches")):
                        continue
                    same = all(follower.get(key) == leader.get(key) for key in (
                        "leader", "term", "replication_nonce", "replication_digest"))
                    if same and follower.get("ledger_digest") == leader["ledger_digest"]:
                        if attempts == 1:
                            print(json.dumps(rows, indent=2, sort_keys=True))
                        return rows
            if attempt + 1 < attempts:
                await asyncio.sleep(2)
        print(json.dumps(rows, indent=2, sort_keys=True))
        raise DistributionError("no healthy leader and matching replica; inspect node logs and replication positions before funding")

    def branch(self, ledger: dict, recipient: str) -> dict | None:
        branches = [b for b in ledger["branches"] if b["sender"] == SENDER
                    and b["receiver"] == recipient]
        if len(branches) > 1:
            raise DistributionError(f"multiple faucet branches found for {recipient}; inspect before retrying")
        if branches and branches[0]["value"] != self.plan["amount"]:
            raise DistributionError(f"preexisting different payment to {recipient}; inspect before retrying")
        known = self.state["known_branches"].get(recipient)
        if known and (not branches or branches[0]["branch_id"] != known):
            raise DistributionError(f"known offer for {recipient} missing from node status; refusing duplicate payment")
        return branches[0] if branches else None

    def reconcile(self, recipient: str, ledger: dict) -> None:
        pending = self.state["in_flight"]
        if pending is None:
            return
        if pending["recipient"] != recipient:
            raise DistributionError(f"unresolved {pending['method']} for {pending['recipient']}; rerun with the same plan")
        branch = self.branch(ledger, recipient)
        method = pending["method"]
        done = (method == "offer" and branch is not None or
                method == "accept" and branch is not None and branch["state"] in ("accepted", "committed", "final") or
                method == "commit" and branch is not None and branch["state"] in ("committed", "final"))
        if not done:
            raise DistributionError(f"uncertain {method} for {recipient}; inspect all nodes before any retry. No second payment sent")
        if branch is not None:
            self.state["known_branches"][recipient] = branch["branch_id"]
        self.state["in_flight"] = None
        self.save()

    async def mutate(self, method: str, recipient: str, params: dict, actor: str) -> None:
        nonces = self.state["last_nonces"]
        nonce = max(time.time_ns() // 1_000_000, int(nonces.get(actor, -1)) + 1)
        nonces[actor] = nonce
        request = {"id": uuid.uuid4().hex[:12], "method": method, "params": params}
        request["auth"] = RequestAuthenticator.sign(request, actor, nonce, self.registry[actor])
        self.state["in_flight"] = {"recipient": recipient, "method": method, "nonce": nonce}
        self.save()  # Reserve nonce and operation before any network I/O.
        response = await self.call(request)
        if "error" in response:
            error = response["error"]
            if error.get("code") == "INVALID_REQUEST":
                self.state["in_flight"] = None  # Definitively rejected, but nonce stays reserved.
                self.save()
            raise DistributionError(f"{recipient} {method} rejected: {error.get('message', error.get('code'))}")
        if method == "offer":
            branch_id = response.get("result", {}).get("branch_id")
            if not isinstance(branch_id, str) or not branch_id:
                raise DistributionError("offer response has no branch ID; reconcile before retrying")
            self.state["known_branches"][recipient] = branch_id
        self.state["in_flight"] = None
        self.save()

    async def fund_one(self, recipient: str) -> str:
        for _ in range(4):
            ledger = await self.snapshot()
            self.reconcile(recipient, ledger)
            branch = self.branch(ledger, recipient)
            balance = ledger["balances"].get(recipient, 0)
            if branch is None:
                if balance != 0:
                    raise DistributionError(f"{recipient} has a balance but no matching branch; refusing a second payment")
                if ledger["balances"].get(SENDER, 0) - ledger["locked"].get(SENDER, 0) < 2 * self.plan["amount"]:
                    raise DistributionError("faucet needs amount plus equal temporary stake")
                await self.mutate("offer", recipient, {"sender": SENDER, "receiver": recipient,
                                                     "value": self.plan["amount"], "ttl": 3600}, SENDER)
            elif branch["state"] == "offered":
                await self.mutate("accept", recipient, {"branch_id": branch["branch_id"],
                                                      "receiver": recipient}, recipient)
            elif branch["state"] == "accepted":
                await self.mutate("commit", recipient, {"branch_id": branch["branch_id"],
                                                      "sender": SENDER, "payload": {}}, SENDER)
            elif branch["state"] in ("committed", "final"):
                return branch["state"]
            else:
                raise DistributionError(f"branch for {recipient} is {branch['state']}; inspect before retrying")
        raise DistributionError(f"{recipient} did not reach committed state; inspect nodes")

    async def run(self, *, wait_finality: bool) -> None:
        for index, recipient in enumerate(self.plan["recipients"], 1):
            stage = await self.fund_one(recipient)
            print(f"{index}/{len(self.plan['recipients'])} {recipient}: {stage}", flush=True)
        if wait_finality:
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                ledger = await self.snapshot()
                final = sum(self.branch(ledger, actor) is not None and
                            self.branch(ledger, actor)["state"] == "final" and
                            ledger["balances"].get(actor, 0) == self.plan["amount"]
                            for actor in self.plan["recipients"])
                if final == len(self.plan["recipients"]):
                    print(f"Verified {final} final balances × {self.plan['amount']} = {self.plan['total']} valueless units.")
                    return
                await asyncio.sleep(5)
            raise DistributionError("not all transfers finalized in 180 seconds; rerun fund to inspect")


def main() -> None:
    parser = argparse.ArgumentParser(description="Operator batch distributor for valueless SplitChain test units")
    parser.add_argument("command", choices=("prepare", "fund", "report", "diagnose"))
    parser.add_argument("--registry", type=Path, default=Path("/operator/accounts.json"))
    parser.add_argument("--output", type=Path, default=Path("/operator/distribution"))
    parser.add_argument("--genesis", type=Path, default=Path("/etc/splitchain/testnet-genesis.json"))
    parser.add_argument("--prefix", default="friend")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--amount", type=int, default=100)
    parser.add_argument("--wait-finality", action="store_true")
    parser.add_argument("--faucet-next-nonce", type=int,
                        help="set higher next faucet nonce if it was already used elsewhere")
    args = parser.parse_args()
    try:
        genesis = GenesisConfig.from_dict(read_json(args.genesis))
        if args.command == "prepare":
            plan = plan_for(args.prefix, args.count, args.amount, genesis)
            prepare(args.registry, args.output, plan)
        else:
            with exclusive(args.output):
                distributor = Distributor(args.registry, args.output, genesis)
                if args.command == "diagnose":
                    asyncio.run(distributor.diagnose())
                elif args.command == "report":
                    ledger = asyncio.run(distributor.snapshot())
                    for actor in distributor.plan["recipients"]:
                        branch = distributor.branch(ledger, actor)
                        print(f"{actor}: {branch['state'] if branch else 'no offer'}, balance={ledger['balances'].get(actor, 0)}")
                else:
                    if args.faucet_next_nonce is not None:
                        if args.faucet_next_nonce < 0:
                            raise DistributionError("nonce cannot be negative")
                        previous = int(distributor.state["last_nonces"].get(SENDER, -1))
                        distributor.state["last_nonces"][SENDER] = max(
                            previous, args.faucet_next_nonce - 1)
                        distributor.save()
                    asyncio.run(distributor.diagnose(attempts=3))
                    asyncio.run(distributor.run(wait_finality=args.wait_finality))
    except (DistributionError, OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"distribution stopped: {exc}\n")


if __name__ == "__main__":
    main()
