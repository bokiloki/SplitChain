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
  independently on replicas, persists monotonic replay nonces and signed history, and replays
  missing entries in order when a replica returns.

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
replica prepares but before Primary commits leaves a durable non-mutating record that Primary
aborts or completes after recovery. This closes the prior apply-before-decision divergence window,
but it is not Byzantine consensus or a crash-safe general-purpose election. The shared HMAC secret must be
replaced by per-node hardware-backed signing
keys before production use. There is no implementation of Overlords, PST triplets, slashing,
failure proofs,
counterproofs, reserve rewards, production-certified nodes, or governance.

The failover safety core requires two signed timeout votes at one committed position and uses
monotonic terms with ordered succession. The reference node now exchanges signed heartbeats,
timeout votes and certificates, persists verified leadership alongside its ledger, fences old
leaders, and lets a certified successor process writes with a surviving replica. CLI clients
can follow a reachable leader endpoint. A candidate refuses promotion if either survivor has
an unresolved prepared mutation, incomplete signed history, or a different ledger state.
When one survivor has a committed signed entry and the other is prepared or behind, it
replays that entry and checks matching state before voting. An uncertain commit is reported
explicitly to the client. If neither survivor has committed, the pending record remains
undecided rather than being treated as final. These conditions can halt progress after a partition; manual
investigation and recovery may be required. The shared HMAC secret lets a compromised node
impersonate any role, wall-clock ticks assume reasonably synchronized hosts, and the election
transport does not yet use independent production-grade identities or Byzantine consensus.
Internal Compose hostnames returned as routes may not resolve from a host client; use the CLI's
`--leader-url` override in that case. Never expose this research cluster to untrusted networks.

`splitd` defaults to loopback. Do not expose it publicly or use it with assets.

## Promotion gates

1. Protocol RFC and ADR accepted.
2. Deterministic tests and adversarial scenarios added.
3. TLA+ safety/liveness properties model-checked.
4. Cross-implementation test vectors pass.
5. Independent security review completed.
