# Per-validator consensus sandbox rehearsal

This build extends the offline protocol test in draft PR #52. It does **not**
enable stake voting or timestamp betting on the public testnet. Each validator
must keep its own sandbox with a complete ledger, membership epoch, public keys,
signed votes, and timestamp commitment history. The intended outer node process
will relay signed events over its authenticated peer network; the sandbox already
validates and persists those events through a local channel. Do not connect the sandbox
directly to the internet or mount another node's writable consensus directory.

Run on an isolated test machine from the repository root:

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m splitchain.sandbox_rehearsal \
  --genesis configs/testnet-genesis.json
.venv/bin/python -m pytest -q
```

For a disposable Docker rehearsal, use the draft branch on a test host:

```sh
docker compose -f compose.sandbox.rehearsal.yaml run --build --rm sandbox-rehearsal
```

This Compose job has no network interface, published ports, live testnet volumes,
or persistent state. It runs as an unprivileged UID on a read-only filesystem;
six independent stores live in a private temporary filesystem until the job exits.
It checks the local protocol logic only. It does **not** deploy six running
validators or exercise real peer delivery and recovery. Do not add this Compose
file to the live testnet Compose invocation.

The rehearsal creates six independent temporary stores. Three proposed
colleague identities each have zero stake. The 7,000,000-unit genesis-locked
reserve backs the three existing sample allocations; the required stake quorum
is 4,666,667. Ten signed commitments are delivered to every store and accepted
in a single bet block certified by more than two-thirds of assigned stake.
Each validator verifies the entire block and its parent digest before a signed
suffix reveal of bet 4 exposes proof material for bets
4 through 10. Every validator checks the whole suffix against previously
accepted commitments. Bets 1 through 3 remain unrevealed. Use
`--output /private/path` to retain the checkpoints (create the parent with
mode 0700). These keys and signatures are
ephemeral; never use them in the live network.

## What is private

The commitment hash and its signed metadata are replicated and therefore
visible to every receiving validator. **No distributed protocol can both give
peers the hash and prevent their operators from reading that copy.** The
originator's secret preimage is stored separately, encrypted with a key that
never enters the peer checkpoint. The original node alone can decrypt that
preimage until it broadcasts a signed reveal. Do not expose sandbox snapshots
through the public status API, and do not share origin keys or private volumes.

The sandbox API accepts signed commitments and signed suffix reveals and returns
aggregate health. It does not offer a history listing. The prototype daemon
listens on a private Unix socket, and the rehearsal **simulates** peer delivery;
the authenticated relay between hosts needs crash/retry and sync testing before
activation. Signed heartbeats report each node's verified round, current ledger
digest and local timestamp; they can flag lag or excessive clock skew, but a
heartbeat cannot change a system clock or certify a block. The existing
operator round driver targets one attempt every ten seconds; it skips missed
slots after a slow attempt. Three finality rounds therefore take at least
about thirty seconds in a healthy network, with further delay during outages.
Unrevealed
preimages limit adaptive betting, but they do not increase the stake quorum or
prove consensus safety. Disclosing bet 4 exposes bets 4 through 10 by design.

## Acceptance gates for live admission

1. Commit the backed stake allocation and its pinned Ed25519 public keys in a
   quorum-certified ledger epoch. Prevent a stake coin from backing two keys at
   once, and require both old and new certificates at an epoch transition.
2. Run the sandbox daemon as an independent network-disabled container;
   connect its private Unix socket to the mutually authenticated relay container.
   Verify delivery retry, replay protection, lag recovery, and independently
   checked sync across real hosts and partitions. The prototype's retry queue
   is in memory, so it does not yet survive a relay restart.
3. Define the authoritative round and target timestamp selection, skew policy,
   commit/reveal deadlines, and resolution/slashing rule; local wall clocks are
   audit data, not a source of consensus ordering.
4. Migrate wallet verification from shared HMAC secrets to public keys, protect
   every old wallet during migration, and validate the original node history
   against multiple independent peers before admitting another voter.
5. Run partitions, delayed reveals, conflicting signatures, restart and disk
   rollback tests on the isolated cluster. Keep the original three-node
   testnet unchanged until these gates pass and a rollback checkpoint is taken.
