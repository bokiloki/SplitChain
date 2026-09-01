# SplitChain

SplitChain is an **experimental protocol and distributed-computing research ecosystem** built around a canonical ledger with bounded two-party execution branches. The project combines the SplitChain protocol, **DistOPS**, SplitChain Services, **TrueLies**, applications, and distributed nodes into a runnable research baseline.

> **Status:** experimental and unaudited. The repository is a working research baseline, not a production blockchain and not suitable for real assets.

![SplitChain ecosystem architecture](docs/images/gallery/splitchain-ecosystem-architecture.png)

## Ecosystem

| Layer | Current implementation |
|---|---|
| Applications | `scplit ecosystem-demo` and WebSocket `ecosystem.demo` RPC |
| DistOPS | Trust-aware scheduling, resource checks, approved workloads and sandbox receipts |
| SplitChain Services | Compute-request lifecycle and receipt registry |
| TrueLies | Three local observers, signed attestations, 2/3 quorum and reputation updates |
| SplitChain protocol | Equal-value staked branch, single commitment and three-round finality |
| Distributed nodes | Three hardened `splitd` containers in Compose |

The intended architecture extends this baseline toward a distributed service and compute ecosystem in which nodes can contribute resources while SplitChain provides deterministic settlement and TrueLies provides an observer/proof layer. DistOPS is the current name of the distributed workload/operating layer; **DistOS is a retired historical name**.

## Getting started

For the complete setup and walkthrough, see **[GETTING_STARTED.md](GETTING_STARTED.md)**.

Requires Python 3.11+.

```bash
git clone https://github.com/bokiloki/SplitChain.git
cd SplitChain
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest -q
scplit ecosystem-demo
```

Windows PowerShell activation:

```powershell
.\.venv\Scripts\Activate.ps1
```

Run the deterministic simulator:

```bash
scplit simulate --seed 42 --steps 500
```

Run a local reference node:

```bash
splitd --host 127.0.0.1 --port 8765
```

Then from another terminal:

```bash
scplit rpc status
scplit rpc offer --params '{"sender":"alice","receiver":"bob","value":10}'
```

Or start the three-node container environment:

```bash
docker compose up --build
scplit rpc ecosystem.demo --url ws://127.0.0.1:8765
```

`splitd` is intentionally unauthenticated and defaults to loopback. Do not expose the reference implementation directly to untrusted networks.

## What works now

- Deterministic branch lifecycle: offer → accept → commit → three-round finality.
- Cancellation and expiry for uncommitted branches.
- Equal-value stake and single-commit enforcement.
- Seeded adversarial simulator with invariant checks.
- Local JSON-over-WebSocket `splitd` reference node.
- `scplit` CLI for simulation, RPC and ecosystem demo.
- DistOPS trust-aware workload scheduling and sandbox receipts.
- SplitChain Services request lifecycle.
- Local TrueLies three-observer 2/3 proof quorum and reputation counter.
- TLA+ safety-core model and TLC configuration.
- Tests, CI, threat model, ADRs, RFC workflow, protocol specification and whitepaper draft.

## Confirmed baseline vs research proposals

The executable baseline deliberately implements only mechanisms that can currently be exercised in code and tests. The broader SplitChain design includes research proposals for deterministic timestamp betting/commit-reveal, timestamp-bound branches, rotating observer/PST selection, Overlord acknowledgements, failure proofs and counterproof locks, slashing, reserve-pool rewards, node certification, governance, peer networking and stronger DistOPS isolation.

These remain proposals until they pass the project workflow and are promoted into the confirmed specification. See [`spec/protocol.md`](spec/protocol.md).

## Repository map

| Path | Purpose |
|---|---|
| `GETTING_STARTED.md` | Installation and first-run guide |
| `spec/` | Normative executable-draft protocol and proposals |
| `docs/` | Whitepaper and architecture imagery |
| `splitchain/` | Model, simulator, WebSocket node, CLI, DistOPS, Services and TrueLies |
| `tests/` | Deterministic unit and integration tests |
| `formal/` | TLA+ model and TLC configuration |
| `adrs/` | Accepted/provisional architectural decisions |
| `rfcs/` | Proposal workflow |
| `security/` | Threat model and promotion gates |

## Architecture gallery

The gallery contains both current diagrams and historical concept artwork. **Current terminology uses DistOPS.** Any image that still visibly says `DistOS` is a legacy visual pending graphical replacement; its old label must not be interpreted as the current component name.

### Ecosystem and application layers

![Ecosystem infographic](docs/images/gallery/splitchain-ecosystem-infographic.png)

![Application layer](docs/images/gallery/splitchain-application-layer-overview.png)

![Distributed cloud ecosystem](docs/images/gallery/splitchain-decentralized-cloud-os-ecosystem.png)

### Services, protocol and consensus

![Services ecosystem](docs/images/gallery/splitchain-services-ecosystem-dashboard.png)

![Protocol architecture](docs/images/gallery/splitchain-protocol-architecture-overview.png)

![OLC-PST research concept](docs/images/gallery/olc-pst-consensus-mechanism-infographic.png)

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

Protocol changes follow:

**RFC → implementation → simulation → attack analysis → formal verification → revision**

Confirmed changes should be traceable to code/tests or an accepted ADR. Open proposals remain clearly labeled until their implementation, simulation, formal-model and security gates pass.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
