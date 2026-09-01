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
- Per-actor monotonic nonces reject replay within one node process.
- Atomic JSON state snapshots use file replacement and restore-time invariant validation.

These are research-node controls, not a substitute for public-key node identities, encrypted
transport, multi-node consensus, or production key management.

## Known critical gaps

No public-key identities or certificate lifecycle; no peer discovery or actual consensus; no
transport encryption, persistent replay window, rate limit, message ordering guarantee, or
resource accounting; no implementation of Overlords, PST triplets, slashing, failure proofs,
counterproofs, reserve rewards, certified nodes, or governance.

`splitd` defaults to loopback. Do not expose it publicly or use it with assets.

## Promotion gates

1. Protocol RFC and ADR accepted.
2. Deterministic tests and adversarial scenarios added.
3. TLA+ safety/liveness properties model-checked.
4. Cross-implementation test vectors pass.
5. Independent security review completed.
