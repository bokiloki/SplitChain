"""Verify signed public OLC pilot receipts without gateway credentials."""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .model import canonical_json


def verify_document(document: dict, pinned_key: str | None = None,
                    pinned_roster: str | None = None) -> tuple[int, int]:
    if document.get("schema") != "olc-receipts/v1":
        raise ValueError("unexpected receipt schema")
    key_hex = document.get("verifier_public_key")
    if pinned_key is not None and key_hex != pinned_key:
        raise ValueError("legacy verifier public key differs from pinned key")
    expected = hashlib.sha256(b"OLC Worker01 test job\n").hexdigest()
    verified = pending = 0
    for receipt in document["receipts"]:
        if receipt.get("verifier_keys") is not None:
            roster = receipt["verifier_keys"]
            if (set(roster) != {"verifier-1", "verifier-2", "verifier-3"}
                    or len(set(roster.values())) != 3 or receipt["quorum_required"] != 2):
                raise ValueError("invalid quorum roster")
            if pinned_roster and hashlib.sha256(canonical_json(roster)).hexdigest() != pinned_roster:
                raise ValueError("verifier roster differs from pinned fingerprint")
            attestations = receipt["attestations"]
            if not isinstance(attestations, dict) or not set(attestations) <= set(roster):
                raise ValueError("invalid verifier votes")
            approvals = denials = 0
            for verifier_id, attestation in attestations.items():
                statement = attestation["statement"]
                if (set(statement) != {"job_id", "node_id", "result_digest", "accepted", "verifier_id"}
                        or statement["verifier_id"] != verifier_id
                        or statement["job_id"] != receipt["job_id"]
                        or statement["node_id"] != receipt["node_id"]
                        or statement["result_digest"] != receipt["result_digest"]
                        or type(statement["accepted"]) is not bool
                        or statement["accepted"] and receipt["result_digest"] != expected):
                    raise ValueError("quorum vote does not match the job")
                public = Ed25519PublicKey.from_public_bytes(bytes.fromhex(roster[verifier_id]))
                public.verify(bytes.fromhex(attestation["signature"]), canonical_json(statement))
                approvals += statement["accepted"]
                denials += not statement["accepted"]
            state = receipt["state"]
            if (state == "quorum_verified" and approvals < 2
                    or state == "disputed" and denials < 2
                    or state == "awaiting_quorum" and (approvals >= 2 or denials >= 2)
                    or state in {"queued", "leased", "rejected"} and attestations
                    or state not in {"quorum_verified", "disputed", "awaiting_quorum",
                                     "queued", "leased", "rejected"}):
                raise ValueError("job state differs from signed quorum")
            if state == "quorum_verified":
                verified += 1
            else:
                pending += 1
            continue
        attestation = receipt.get("attestation")
        if attestation is None:
            pending += 1
            continue
        if key_hex is None:
            raise ValueError("missing legacy verifier key")
        public_key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key_hex))
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
