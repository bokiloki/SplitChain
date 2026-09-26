"""Offline six-validator stake and hashed-bet rehearsal for an isolated host."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .membership import StakeMembership
from .model import GenesisConfig, Ledger, ProtocolError
from .origin_secrets import OriginBetSecrets
from .stake_vault import SandboxConsensusStore
from .stake_votes import StakeDecision, StakeVoteBook, sign_vote
from .timestamp_bets import TimestampBetBook, bet_hash, next_secret, sign_commit, sign_suffix


def rehearse(genesis_path: str | Path, directory: str | Path) -> dict:
    genesis = GenesisConfig.from_dict(json.loads(Path(genesis_path).read_text()))
    reserve = "testnet_locked_reserve"
    ledger = Ledger(genesis=genesis)
    backing = ledger.balances[reserve]
    first = backing // 3
    names = ("primary", "secondary", "tertiary",
             "colleague-primary", "colleague-secondary", "colleague-tertiary")
    allocations = tuple(zip(names, (first, first, backing - 2 * first, 0, 0, 0), strict=True))
    membership = StakeMembership.from_locked_reserve(ledger, reserve, 0, allocations)
    private = {name: Ed25519PrivateKey.generate() for name in names}
    public = {name: key.public_key() for name, key in private.items()}
    books = {name: StakeVoteBook(membership, genesis.network_id, genesis.digest(), public)
             for name in names}
    stores = {name: SandboxConsensusStore(Path(directory) / name / "consensus.json",
                                          genesis=genesis, membership=membership, keys=public)
              for name in names}
    value = min(first, 100)
    if value < 1:
        raise ProtocolError("sandbox rehearsal needs positive validator stake")
    quorum_decision = StakeDecision(
        genesis.network_id, 0, books["primary"].epoch_digest,
        "mutation", 1, "sandbox-transfer", value,
    )
    signed_votes = [sign_vote(private[name], name, quorum_decision) for name in names[:3]]
    signer_votes = {name: [] for name in names}
    for name, book in books.items():
        for signed in signed_votes:
            signer_votes[name].append(book.submit(signed))
        book.certificate(quorum_decision).verify(book)
    # Simulated node-to-node relay: distribute ten signed commitments, never the seed.
    origin = names[0]
    seed = "sandbox-rehearsal-secret-unique-32-characters"
    (Path(directory) / origin).mkdir(mode=0o700, parents=True, exist_ok=True)
    origin_secrets = OriginBetSecrets(Path(directory) / origin / "origin-secrets",
                                      origin, os.urandom(32))
    origin_secrets.save(1, books[origin].epoch_digest, seed)
    target_ms = 1_700_000_000_000
    signed_bets = []
    secret = seed
    for index in range(1, 11):
        signed_bets.append(sign_commit(
            private[origin], voter=origin, epoch_digest=books[origin].epoch_digest,
            position=index, transaction_digest=f"sandbox-transfer-{index}", value=value,
            target_round=3, target_timestamp_ms=target_ms + index,
            commitment=bet_hash(books[origin].epoch_digest,
                                f"sandbox-transfer-{index}", target_ms + index, secret),
            series_id="sandbox-ten-bets", sequence_index=index, sequence_length=10,
        ))
        secret = next_secret(secret)
    for name, book in books.items():
        bets = TimestampBetBook(book)
        for signed in signed_bets:
            bets.commit(signed, observed_round=0)
        stores[name].save(Ledger(genesis=genesis), book, 1, bets)
    for name, store in stores.items():
        _, recovered, position, bets = store.load()
        if position != 1 or not recovered.certificate(quorum_decision):
            raise ProtocolError(f"sandbox node {name} did not recover quorum")
        if any(bets.commits[(origin, signed.position)][0].commitment != signed.commitment
               for signed in signed_bets):
            raise ProtocolError(f"sandbox node {name} did not receive commitment")
        if seed in store.path.read_text():
            raise ProtocolError("sandbox disclosed a secret before reveal")
    fourth_secret = origin_secrets.read(1, books[origin].epoch_digest)
    for _ in range(3):
        fourth_secret = next_secret(fourth_secret)
    reveal = sign_suffix(
        private[origin], origin, books[origin].epoch_digest,
        "sandbox-ten-bets", 4, 10, fourth_secret,
    )
    for name, store in stores.items():
        recovered_ledger, recovered_book, position, bets = store.load()
        bets.reveal_suffix(reveal, observed_round=3)
        store.save(recovered_ledger, recovered_book, position, bets)
    return {
        "schema": "splitchain/sandbox-rehearsal/v1",
        "nodes": len(names), "independent_stores": len(stores),
        "total_backed_stake": membership.total_stake,
        "quorum_stake": membership.quorum_stake,
        "candidate_weight": sum(dict(allocations)[name] for name in names[3:]),
        "votes_before_quorum": signer_votes[origin][:2],
        "quorum_after_third_vote": signer_votes[origin][2],
        "all_hashes_match": len({tuple(store.load()[3].commits[(origin, signed.position)][0]
                                       .commitment for signed in signed_bets)
                                 for store in stores.values()}) == 1,
        "suffix_revealed": "4-10",
        "all_reveals_verified": all(store.load()[3].suffixes[(origin, "sandbox-ten-bets")][0]
                                    == reveal
                                    for store in stores.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rehearse independent sandbox consensus")
    parser.add_argument("--genesis", default="configs/testnet-genesis.json")
    parser.add_argument("--output", help="Private directory to retain six sandbox checkpoints")
    args = parser.parse_args()
    if args.output:
        result = rehearse(args.genesis, args.output)
    else:
        with tempfile.TemporaryDirectory(prefix="splitchain-sandbox-") as directory:
            result = rehearse(args.genesis, directory)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
