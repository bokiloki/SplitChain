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
- The recovery model requires 2/3 signed failure claims, preserves a counterproof window,
  advances roles deterministically, and aborts explicitly after Tertiary failure.
- The DistOPS policy model binds signed manifests to complete workload requests, applies
  risk-dependent trust/reputation gates, emits explicit isolation/quotas/network/secrets policy,
  and generates domain-separated completion proofs.
- The connected reference cluster authenticates Primary mutation envelopes with a generated
  HMAC secret, requires a 2/3 acknowledgement quorum including Primary, validates transitions
  independently on replicas, and persists monotonic replay nonces with ledger state.

These are research-node controls. Certificates and gossip currently use deterministic local HMAC
keys and are not a substitute for public-key node identities, encrypted transport, multi-node
consensus, hardware-backed keys, revocation infrastructure, or production key management.
DistOPS receipts describe required enforcement but do not themselves launch or attest a real
container/microVM runtime. The opt-in runtime adapter validates deny-only container execution and
attested controls, but production daemon hardening and independent remote attestation remain open.
The research adapter now rejects attestation-nonce replay, restricts allowlisted traffic to a
trusted high-reputation egress gateway, blocks consensus endpoint names, and models one-time
expiring secret leases whose in-memory buffers are cleared after consumption.

## Known critical gaps

No certificate revocation, rate limit, global message ordering guarantee, or resource accounting.
The acknowledgement quorum is not yet crash-safe consensus: an acknowledgement lost after a
replica persists but before Primary commits can leave nodes divergent, and automatic catch-up is
not yet implemented. The shared HMAC secret must be replaced by per-node hardware-backed signing
keys before production use. There is no implementation of Overlords, PST triplets, slashing,
failure proofs,
counterproofs, reserve rewards, production-certified nodes, or governance.

`splitd` defaults to loopback. Do not expose it publicly or use it with assets.

## Promotion gates

1. Protocol RFC and ADR accepted.
2. Deterministic tests and adversarial scenarios added.
3. TLA+ safety/liveness properties model-checked.
4. Cross-implementation test vectors pass.
5. Independent security review completed.
