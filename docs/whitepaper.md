# SplitChain: Temporary Execution Branches for a Canonical Ledger

## Research draft v0.1

SplitChain explores whether two participants can temporarily isolate a single transaction from a
canonical ledger, secure it with an equal-value stake, and deterministically merge the result. The
goal is bounded concurrency without treating every branch as a permanent competing chain.

The current repository proves only a small claim: a deterministic state machine can enforce equal
stake, single-use branches, expiry, cancellation, and three-round finalization while conserving
supply. A seeded simulator repeatedly checks these invariants, a WebSocket process exposes the
state machine for local multi-process experiments, and a TLA+ model states the same safety core.

### Architecture

- **Canonical ledger:** owns balances and finalized transaction fingerprints.
- **Temporary branch:** binds sender, receiver, value, stake, expiry, and one commitment.
- **splitd:** deliberately small local reference node.
- **scplit:** simulator and JSON-RPC command-line client.
- **Formal model:** checks conservation, equal stake, and terminal-state safety.

### Security posture

The implementation is experimental and unaudited. It lacks cryptographic identities, persistent
storage, peer consensus, authenticated transport, rate limiting, and Byzantine-fault handling.
It must not hold assets or be exposed to untrusted networks.

### Research workflow

Changes advance through: **RFC → implementation → simulation → attack analysis → revision**.
Accepted decisions receive ADRs and versioned test vectors. The next milestone is to specify the
observer/finality mechanism precisely enough to compare simulation traces against TLA+ behavior.

