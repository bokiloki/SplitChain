"""Validate a pinned stake epoch configuration before starting an isolated sandbox."""

from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .membership import StakeMembership
from .model import GenesisConfig, Ledger, ProtocolError
from .stake_votes import StakeVoteBook


def load_manifest(path: str | Path, genesis: GenesisConfig,
                  expected_epoch_digest: str) -> tuple[StakeMembership, dict[str, Ed25519PublicKey]]:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        if (not isinstance(document, dict) or set(document) != {
            "schema", "network_id", "genesis_digest", "reserve_account",
            "epoch", "allocations", "public_keys",
        } or document["schema"] != "splitchain/stake-manifest/v1"
                or document["network_id"] != genesis.network_id
                or document["genesis_digest"] != genesis.digest()
                or not isinstance(document["allocations"], dict)
                or not isinstance(document["public_keys"], dict)
                or set(document["allocations"]) != set(document["public_keys"])):
            raise ProtocolError("stake manifest does not match pinned genesis")
        allocations = tuple(sorted(document["allocations"].items()))
        membership = StakeMembership.from_locked_reserve(
            Ledger(genesis=genesis), document["reserve_account"],
            document["epoch"], allocations,
        )
        keys = {name: Ed25519PublicKey.from_public_bytes(
            base64.b64decode(value, validate=True)
        ) for name, value in document["public_keys"].items()}
        book = StakeVoteBook(membership, genesis.network_id, genesis.digest(), keys)
        if book.epoch_digest != expected_epoch_digest:
            raise ProtocolError("stake manifest digest differs from pinned epoch")
        return membership, keys
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError,
            binascii.Error) as exc:
        raise ProtocolError("invalid or unpinned stake manifest") from exc
