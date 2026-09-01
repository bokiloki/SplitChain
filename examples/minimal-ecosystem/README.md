# Minimal ecosystem reference

This standard-library-only research example connects a deterministic five-node
network, observer-quorum branch activation, TrueLies evidence verification, and
DistOPS trust-gated sandbox policy. It is intentionally smaller than the main
SplitChain implementation and is not a production network or container launcher.

```bash
cd examples/minimal-ecosystem
PYTHONPATH=. python3 demo.py
PYTHONPATH=. python3 -m unittest discover -s tests -v
```
