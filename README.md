# SplitChain

SplitChain is an **experimental protocol research project** for a canonical ledger with bounded,
two-party execution branches. This repository is a working v0.1 baseline, not a production
blockchain and not suitable for real assets.

## What works now

- Deterministic branch lifecycle: offer, accept, commit, cancel/expire, three-round finality.
- Equal-value stake and single-commit enforcement.
- Seeded adversarial simulator with invariant checks.
- Local JSON-over-WebSocket `splitd` reference node.
- `scplit` CLI for simulations and RPC calls.
- TLA+ safety-core model and configuration.
- Tests, CI, threat model, ADRs, RFC template, protocol spec, and whitepaper draft.

## Confirmed baseline vs open research

The executable baseline covers only the mechanics above. TrueLies/OLC-PST, Overlords, rotating
PST triplets, 2/3 acknowledgements, commit-reveal timing, failure/counterproofs, reputation,
slashing, reserve-pool rewards, node certification, voting, and production networking are open
proposals documented in [`spec/protocol.md`](spec/protocol.md). They are not presented as working.

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

## Development process

Protocol work follows:

**RFC → implementation → simulation → attack analysis → revision**

Confirmed changes must be traceable to code/tests or an accepted ADR. Open proposals must remain
clearly labeled until their implementation, simulation, formal-model, and security gates pass.

## License

Apache-2.0. See [`LICENSE`](LICENSE).
