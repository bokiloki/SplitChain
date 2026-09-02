# Getting Started with SplitChain

SplitChain is an experimental research ecosystem. The current repository provides a runnable local vertical slice across the application layer, DistOPS, SplitChain Services, TrueLies, the SplitChain protocol, and distributed reference nodes. It is not production-ready and must not be used to hold real assets.

## 1. Requirements

Choose either a native Python setup or Docker.

### Native

- Python 3.11+
- Git

### Containers

- Docker Engine / Docker Desktop
- Docker Compose v2

## 2. Clone and install

```bash
git clone https://github.com/bokiloki/SplitChain.git
cd SplitChain
python -m venv .venv
```

Linux/macOS:

```bash
. .venv/bin/activate
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

Install the project and development dependencies:

```bash
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

## 3. Verify the baseline

```bash
pytest -q
scplit simulate --seed 42 --steps 500
```

The simulator exercises the deterministic branch state machine and checks the implemented safety invariants.

## 4. Run the complete ecosystem demo

```bash
scplit ecosystem-demo
```

This passes one request through:

1. Application request
2. DistOPS workload selection and sandbox receipt
3. SplitChain Services lifecycle
4. TrueLies observer proof/quorum
5. SplitChain branch commitment and three-round finality
6. Distributed-node representation

## 5. Run a local reference node

Terminal 1:

```bash
splitd --host 127.0.0.1 --port 8765
```

To persist ledger state across restarts, add `--state ./data/ledger.json`.
Successful mutations atomically replace the state file; restart validates locked
funds, equal stake, and canonical branch origins before accepting the snapshot.

Terminal 2:

```bash
scplit rpc status
scplit rpc offer --params '{"sender":"alice","receiver":"bob","value":10}'
```

`splitd` remains a research reference node. Optional HMAC request authentication
is available through `ReferenceNode(auth_secrets=...)` for controlled experiments,
with actor binding and restart-persistent nonce replay rejection when `--state` is used.
The `identity` and `network` modules also provide a deterministic local certificate
and branch-scoped gossip model for three-node experiments. This is not production
identity, PKI, encrypted transport, consensus, or key management; keep the server on loopback
unless you are working in an isolated test environment.

For encrypted node experiments, enable TLS 1.3 mutual authentication on both ends:

```bash
splitd --host 127.0.0.1 --port 8765 \
  --tls-cert node.crt --tls-key node.key --tls-ca ca.crt --tls-peers peers.json
scplit rpc status --url wss://node.example:8765 \
  --tls-cert client.crt --tls-key client.key --tls-ca ca.crt
```

All three TLS files are mandatory when TLS is enabled. The client verifies the server
hostname, and the server rejects peers without a certificate signed by the configured CA.
When `--tls-peers` is supplied, the server additionally hashes the presented DER
certificate and requires an exact entry in the registry:

```json
{"peers":[{"node_id":"validator-a","certificate_sha256":"<64 hex characters>","roles":["primary"]}]}
```

Node IDs and certificate fingerprints must be unique, and roles are restricted to
`primary`, `secondary`, `tertiary`, `overlord`, or `client`.
The pinned roles are enforced for every RPC request: `client` may submit transaction
methods, consensus and `overlord` roles may advance rounds, and all authorized peers may
read status and the ecosystem demonstration. Unknown methods are denied before dispatch.

## 6. Run the lightweight three-node Docker environment

```bash
cp .env.example .env
sed -i "s/replace-with-at-least-32-random-characters/$(openssl rand -hex 32)/" .env
docker compose up --build -d
docker compose ps
```

The Compose environment starts three hardened reference-node containers on loopback-only
ports 8765, 8766, and 8767. Each node is limited to 0.5 CPU, 256 MiB of memory,
128 processes, bounded logs and a 16 MiB temporary filesystem. Health checks and
automatic restarts are enabled, and each ledger is stored in its own named volume.
This is the supported lightweight single-server deployment; Kubernetes is not required.
The generated cluster secret authenticates internal replication messages and is excluded
from Git. Do not reuse it outside this test cluster.

Try the ecosystem RPC:

```bash
scplit rpc ecosystem.demo --url ws://127.0.0.1:8765
```

Verify that the primary can reach both other containers:

```bash
scplit rpc cluster.status --url ws://127.0.0.1:8765
```

The response reports the primary ledger and an `available` or `unavailable` result for
the Secondary and Tertiary nodes. Peer probes have a three-second timeout and partial
failure does not block results from healthy nodes.

Mutating RPCs must be sent to Primary. It durably records a signed proposal, sends `prepare`
to both replicas, and requires at least one prepared replica for a 2/3 quorum including itself.
Prepare validates the transition without changing balances. Primary then persists its commit
decision before sending `commit`; replicas apply only a matching prepared envelope. Prepared
records, commit history and replay nonces survive restarts, while failed quorum attempts are
explicitly aborted without changing the ledger.
After an offline replica returns, run
`scplit rpc cluster.sync --url ws://127.0.0.1:8765`; Primary reads each replica's durable
position and replays only the missing, independently verified envelopes in order.

Stop the environment with:

```bash
docker compose down
```

Named ledger volumes survive `docker compose down`. Do not add `--volumes` unless you
intend to permanently delete the local node state.

## 7. Repository layout

- `splitchain/` — executable model, simulator, CLI, WebSocket node, DistOPS, services and TrueLies reference implementation
- `spec/` — protocol specification and proposed mechanisms
- `docs/` — whitepaper and architecture imagery
- `tests/` — deterministic tests
- `formal/` — TLA+ model and TLC configuration
- `security/` — threat model and security gates
- `adrs/` — architecture decisions
- `rfcs/` — proposal workflow

## 8. Terminology

The distributed operating-system/workload layer is named **DistOPS**. Older visuals or historical artifacts may still contain the former name **DistOS**; those are legacy materials and should not be treated as current terminology.

## 9. Visual learning path

Use this order to move from the ecosystem overview to protocol safety:

1. [Ecosystem architecture](docs/images/gallery/splitchain-ecosystem-architecture.png)
2. [Application layer](docs/images/gallery/splitchain-application-layer-overview.png)
3. [DistOPS operating modes](docs/images/gallery/DistOPSModes.png)
4. [Node and network architecture](docs/images/gallery/splitchain-node-network-architecture.png)
5. [Two permitted branch types](docs/images/gallery/SplitChain%20—%20two%20permitted%20branch%20types.png)
6. [Branch lifecycle](docs/images/gallery/splitchain-branch-lifecycle.png)
7. [Observer quorum](docs/images/gallery/Observer%20quorum%20gates%20split%20activation.png)
8. [Deterministic candidate selection](docs/images/gallery/SplitChain_%20deterministic%20tie-aware%20candidate%20selection.png)
9. [Failure proof and counterproof](docs/images/gallery/splitchain-failure-proof-and-counterproof.png)
10. [Core safety invariants](docs/images/gallery/splitchain-core-safety-invariants.png)
11. [Threat model and defenses](docs/images/gallery/splitchain-threat-model-and-defenses.png)

![SplitChain node and network architecture](docs/images/gallery/splitchain-node-network-architecture.png)

The diagrams include broader research proposals. Use the executable baseline,
tests and `spec/protocol.md` to determine which mechanisms are currently
implemented.

## 10. What to work on next

The current executable baseline is deliberately smaller than the proposed full protocol. Major research areas include deterministic timestamp betting/commit-reveal, rotating observer selection, Overlord acknowledgements, failure proofs and counterproofs, slashing and reserve-pool rewards, node certification, governance, persistence, authenticated peer networking, and stronger DistOPS sandbox isolation.

Before promoting a proposal into the confirmed protocol, follow the project workflow:

**RFC → implementation → simulation → attack analysis → formal verification → revision**

See `README.md`, `spec/protocol.md`, and `docs/whitepaper.md` for the current project status.
