# SplitChain: Temporary Execution Branches for a Canonical Ledger

## Research draft v0.2

SplitChain explores whether participants can temporarily isolate a bounded state transition from a canonical ledger, secure it with stake, and deterministically merge the result. The broader project combines the **SplitChain protocol**, **DistOPS**, **SplitChain Services**, **TrueLies**, applications, and distributed nodes into an experimental distributed-computing ecosystem.

The current repository proves a deliberately limited executable claim: a deterministic state machine can enforce equal stake, single-use branches, expiry, cancellation, and three-round finalization while conserving supply. A seeded simulator checks these invariants, a WebSocket reference node exposes the state machine for local experiments, and a TLA+ model states the same safety core.

## Ecosystem architecture

- **Application layer:** clients and services that request work or protocol operations.
- **DistOPS:** distributed workload orchestration, trust-aware node selection, resource checks and sandbox receipts. DistOPS is the current component name; DistOS is retired terminology.
- **SplitChain Services:** service-request lifecycle connecting applications, DistOPS and proofs.
- **TrueLies:** observer/proof layer. The executable baseline currently provides a deterministic local three-observer 2/3 quorum, not production Byzantine consensus.
- **SplitChain protocol:** canonical ledger plus temporary deterministic execution branches.
- **Distributed nodes:** reference nodes and future compute/service participants.

## Implemented protocol core

A temporary branch binds sender, receiver, value, equal-value stake, expiry, and one transaction commitment. Implemented branches move through `offered → accepted → committed → final`, with cancellation or expiry available before commitment. Finality currently requires three deterministic rounds.

The implementation includes:

- equal-value stake enforcement;
- one commitment per branch;
- canonical commitment hashing;
- deterministic expiry/cancellation;
- supply-conservation checks;
- seeded adversarial simulation;
- local WebSocket RPC and CLI;
- TLA+ safety-core model;
- an executable application → DistOPS → Services → TrueLies → SplitChain vertical slice.

## Extended research design

The project is investigating mechanisms beyond the confirmed executable core, including deterministic timestamp betting/commit-reveal, binding branch activity to timestamp targets, rotating observer/PST triplets, Overlord acknowledgements, failure proofs and counterproofs, reputation and slashing, reserve-pool rewards, certified nodes, governance, peer networking, and rejoining behavior for nodes that temporarily leave the canonical chain.

A related design principle is that historical commitments can act as accumulated cryptographic history while only the required reveal material is exposed for the active timing decision. These mechanisms remain research proposals until precisely specified and validated.

DistOPS is also intended to support heterogeneous compute nodes while preventing distributed workloads or remote sessions from taking control of hosts. The production design therefore requires stronger sandbox boundaries, least privilege, workload allowlisting, resource limits, network isolation, reputation-aware scheduling and additional isolation for untrusted nodes.

## Security posture

The implementation is experimental and unaudited. The reference node lacks production cryptographic identities, durable consensus storage, authenticated peer transport, mature Sybil resistance, complete Byzantine-fault handling, and production DoS controls. It must not hold real assets or be exposed directly to untrusted networks.

## Research workflow

Changes advance through:

**RFC → implementation → simulation → attack analysis → formal verification → revision**

Accepted decisions receive ADRs and versioned tests. Proposed mechanisms must remain clearly distinguished from implemented behavior so that architecture artwork, documentation and demonstrations do not overstate the security or maturity of the protocol.
