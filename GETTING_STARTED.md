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

Terminal 2:

```bash
scplit rpc status
scplit rpc offer --params '{"sender":"alice","receiver":"bob","value":10}'
```

`splitd` is intentionally a research reference node and is unauthenticated. Keep it bound to loopback unless you are working in an isolated test environment.

## 6. Run the three-node Docker environment

```bash
docker compose up --build
```

The Compose environment starts three hardened reference-node containers on local ports 8765, 8766, and 8767.

Try the ecosystem RPC:

```bash
scplit rpc ecosystem.demo --url ws://127.0.0.1:8765
```

Stop the environment with:

```bash
docker compose down
```

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

## 9. What to work on next

The current executable baseline is deliberately smaller than the proposed full protocol. Major research areas include deterministic timestamp betting/commit-reveal, rotating observer selection, Overlord acknowledgements, failure proofs and counterproofs, slashing and reserve-pool rewards, node certification, governance, persistence, authenticated peer networking, and stronger DistOPS sandbox isolation.

Before promoting a proposal into the confirmed protocol, follow the project workflow:

**RFC → implementation → simulation → attack analysis → formal verification → revision**

See `README.md`, `spec/protocol.md`, and `docs/whitepaper.md` for the current project status.
