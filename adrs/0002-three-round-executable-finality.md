# ADR-0002: Three-round finality in the executable baseline

- Status: Provisional
- Date: 2026-08-31

## Decision

The executable model finalizes a committed branch after three deterministic rounds.

## Consequences

Tests and the TLA+ model share a measurable boundary. The meaning of a round and the observer
certificate needed for production finality remain open and require a separate RFC.

