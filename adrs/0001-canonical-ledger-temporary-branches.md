# ADR-0001: Canonical ledger with bounded temporary branches

- Status: Accepted for v0.1 research baseline
- Date: 2026-08-31

## Decision

Use one canonical ledger. A branch is a bounded two-party execution session for one transaction,
locks value plus equal stake, and terminates by finalization, cancellation, or expiry.

## Consequences

This narrows the safety surface and makes conservation model-checkable. It does not solve observer
selection, network consensus, censorship, privacy, or liveness under Byzantine behavior.

