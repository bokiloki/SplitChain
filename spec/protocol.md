# SplitChain Protocol Specification v0.1

Status: **experimental executable draft**. This document distinguishes implemented v0.1
mechanics from research proposals. It is not a production or security claim.

## 1. Model

SplitChain maintains one canonical UTXO-inspired ledger and temporary deterministic execution
branches between two participants. A branch is an offer to perform exactly one state transition;
it is not an independent currency or unbounded fork.

## 2. Implemented v0.1 transition system

States are `offered → accepted → committed → final`. An uncommitted branch can instead become
`cancelled` or `expired`.

Normative invariants in the executable model:

1. The origin locks `value + stake`, and `stake == value`.
2. Sender and receiver are distinct and explicitly named.
3. A branch accepts no more than one transaction commitment.
4. A commitment is the SHA-256 digest of canonical JSON.
5. Finality occurs after three deterministic rounds.
6. Offers and accepted branches expire at their declared round and unlock funds.
7. Finalization moves only `value`; the equal stake is unlocked.
8. Total supply cannot change through any implemented transition.

## 3. Message surface

The reference node accepts JSON requests `{id, method, params}` over WebSocket. Implemented
methods: `status`, `offer`, `accept`, `commit`, `cancel`, and `advance`. This surface has no peer
authentication and must bind to loopback by default.

## 4. Open proposals (not implemented)

- Production TrueLies/OLC-PST observer selection and rotating triplets. The local ecosystem only
  implements deterministic HMAC attestations from three configured observers with a 2/3 quorum.
- At least three Overlords with 2/3 acknowledgements.
- Commit-reveal timing targets, previous-winner window application, and Secondary takeover.
- Failure proofs, counterproof locks, slashing, reserve-pool rewards, and reputation aging.
- Certified main-chain signing, transferable node addresses, and voting-share rules.
- Joint cancellation fees, revocation windows, and immediate branch merge semantics.
- Networking, persistence, signatures, Sybil resistance, privacy, and production DoS controls.

Each proposal requires an RFC, ADR, simulator experiment, adversarial analysis, and formal-model
change before promotion into the confirmed specification.
