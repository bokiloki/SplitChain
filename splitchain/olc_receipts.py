"""Verify signed public OLC pilot receipts without gateway credentials."""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .model import canonical_json


def verify_document(document: dict, pinned_key: str | None = None) -> tuple[int, int]:
    if document.get("schema") != "olc-receipts/v1":
        raise ValueError("unexpected receipt schema")
    key_hex = document["verifier_public_key"]
    if pinned_key is not None and key_hex != pinned_key:
        raise ValueError("verifier public key differs from pinned key")
    public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
    expected = hashlib.sha256(b"OLC Worker01 test job\n").hexdigest()
    verified = pending = 0
    for receipt in document["receipts"]:
        attestation = receipt.get("attestation")
        if attestation is None:
            pending += 1
            continue
        statement = attestation["statement"]
        if (set(statement) != {"job_id", "node_id", "result_digest", "accepted"}
                or statement["job_id"] != receipt["job_id"]
                or statement["node_id"] != receipt["node_id"]
                or statement["result_digest"] != receipt["result_digest"]
                or type(statement["accepted"]) is not bool
                or statement["accepted"] != (receipt["result_digest"] == expected)):
            raise ValueError("verifier statement does not match the job receipt")
        public_key.verify(bytes.fromhex(attestation["signature"]), canonical_json(statement))
        verified += 1
    return verified, pending
