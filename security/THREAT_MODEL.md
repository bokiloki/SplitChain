# Threat model v0.1

## Protected properties

- Supply conservation and non-negative balances.
- Equal-value origin stake.
- At most one commitment per branch.
- Funds unlock on cancellation/expiry and transfer once on finalization.

## In-scope adversarial simulator actions

Random invalid ordering, duplicate transitions, cancellation races, insufficient funding, and
expiry/finality interleavings. Runs are seeded and reproducible.

## Implemented reference defenses

- Optional HMAC-authenticated RPC envelopes bind request ID, method, parameters, actor and nonce.
- Per-actor monotonic nonces reject replay and persist with ledger mutations across restarts.
- Atomic JSON state snapshots use file replacement and restore-time invariant validation.
- Local certificate and three-node gossip models reject expired roles, invalid signatures,
  cross-certificate senders, and stale or duplicate branch-scoped sequences.
- The finality model requires certified 2/3 acknowledgements in each of three successive rounds,
  rejects out-of-round/cross-branch votes, and quarantines conflicting candidate voters.

These are research-node controls. Certificates and gossip currently use deterministic local HMAC
keys and are not a substitute for public-key node identities, encrypted transport, multi-node
consensus, hardware-backed keys, revocation infrastructure, or production key management.

## Known critical gaps

No public-key identities, certificate revocation, or peer discovery; no actual consensus,
transport encryption, rate limit, global message ordering guarantee, or resource accounting;
no implementation of Overlords, PST triplets, slashing, failure proofs,
counterproofs, reserve rewards, production-certified nodes, or governance.

`splitd` defaults to loopback. Do not expose it publicly or use it with assets.

## Promotion gates

1. Protocol RFC and ADR accepted.
2. Deterministic tests and adversarial scenarios added.
3. TLA+ safety/liveness properties model-checked.
4. Cross-implementation test vectors pass.
5. Independent security review completed.
