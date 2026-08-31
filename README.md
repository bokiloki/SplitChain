# SplitChain

SplitChain is an **experimental protocol research project** for a canonical ledger with bounded,
two-party execution branches. This repository is a working v0.1 baseline, not a production
blockchain and not suitable for real assets.

![SplitChain ecosystem architecture](docs/images/gallery/splitchain-ecosystem-architecture.png)

## Working ecosystem

The repository contains a runnable vertical slice through every ecosystem layer:

| Layer | Working implementation |
|---|---|
| Applications | `scplit ecosystem-demo` and WebSocket `ecosystem.demo` RPC |
| DistOPS | Trust-aware scheduling, resource checks, approved images, sandbox receipts |
| Services | Compute request lifecycle and receipt registry |
| TrueLies | Three observers, signed attestations, 2/3 quorum, reputation updates |
| SplitChain protocol | Staked branch, single commitment, merge after three rounds |
| Distributed nodes | Three hardened `splitd` containers in Compose |

Run every layer in one verified flow:

```bash
scplit ecosystem-demo
```

Or start three local nodes:

```bash
docker compose up --build
scplit rpc ecosystem.demo --url ws://127.0.0.1:8765
```

## What works now

- Deterministic branch lifecycle: offer, accept, commit, cancel/expire, three-round finality.
- Equal-value stake and single-commit enforcement.
- Seeded adversarial simulator with invariant checks.
- Local JSON-over-WebSocket `splitd` reference node.
- `scplit` CLI for simulations and RPC calls.
- TLA+ safety-core model and configuration.
- Tests, CI, threat model, ADRs, RFC template, protocol spec, and whitepaper draft.

## Confirmed baseline vs open research

The executable ecosystem includes a local three-observer TrueLies proof quorum and reputation
counter. Production OLC-PST selection, rotating triplets, commit-reveal timing, Byzantine failure
and counterproofs, slashing, reserve-pool rewards, node certification, governance, and peer
networking remain open proposals in [`spec/protocol.md`](spec/protocol.md).

## Quick start

Requires Python 3.11+.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
scplit simulate --seed 42 --steps 500
```

Run a local node and query it from a second terminal:

```bash
splitd --host 127.0.0.1 --port 8765
scplit rpc status
scplit rpc offer --params '{"sender":"alice","receiver":"bob","value":10}'
```

`splitd` is intentionally unauthenticated and defaults to loopback. Do not expose it publicly.

## Repository map

| Path | Purpose |
|---|---|
| `spec/` | Normative executable-draft protocol |
| `docs/` | Whitepaper source |
| `splitchain/` | Model, simulator, WebSocket node, and CLI |
| `tests/` | Deterministic unit and integration tests |
| `formal/` | TLA+ model and TLC configuration |
| `adrs/` | Accepted/provisional architectural decisions |
| `rfcs/` | Proposal workflow |
| `security/` | Threat model and promotion gates |

## Architecture gallery

The original project visuals are preserved in [`docs/images/gallery`](docs/images/gallery).

### Ecosystem and application layers

![Ecosystem infographic](docs/images/gallery/splitchain-ecosystem-infographic.png)

![Application layer](docs/images/gallery/splitchain-application-layer-overview.png)

![Decentralized cloud OS](docs/images/gallery/splitchain-decentralized-cloud-os-ecosystem.png)

![SplitChain, DistOS and TrueLies](docs/images/gallery/neon-blockchain-triptych-splitchain-distos-truelies.png)

### Services, protocol and consensus

![Services ecosystem](docs/images/gallery/splitchain-services-ecosystem-dashboard.png)

![Protocol architecture](docs/images/gallery/splitchain-protocol-architecture-overview.png)

![OLC-PST consensus](docs/images/gallery/olc-pst-consensus-mechanism-infographic.png)

![TrueLies consensus](docs/images/gallery/truelies-consensus-flow.png)

### Nodes, lifecycle and security

![Distributed nodes](docs/images/gallery/splitchain-distributed-nodes-infographic.png)

![Node communication](docs/images/gallery/splitchain-neon-network-communication-diagram.png)

![Branch lifecycle](docs/images/gallery/splitchain-branch-lifecycle.png)

![Threat model](docs/images/gallery/splitchain-threat-model-and-defenses.png)

![Core safety invariants](docs/images/gallery/splitchain-core-safety-invariants.png)

### Dashboard, roadmap and scale

![Dashboard](docs/images/gallery/splitchain-neon-blockchain-dashboard.png)

![Development roadmap](docs/images/gallery/splitchain-development-and-verification-roadmap.png)

![Ecosystem in numbers](docs/images/gallery/splitchain-ecosystem-in-numbers.png)

## Development process

Protocol work follows:

**RFC → implementation → simulation → attack analysis → revision**

Confirmed changes must be traceable to code/tests or an accepted ADR. Open proposals must remain
clearly labeled until their implementation, simulation, formal-model, and security gates pass.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
