# Security hardening roadmap

## Wallet credentials

The friend distributor currently writes one mode-0600 SMS draft per wallet. This
is acceptable only for the valueless pilot. For a longer-lived network, encrypt
the credential export with an operator supplied key kept outside the server,
create one-time delivery records, and delete each export after the recipient
acknowledges receipt. Never publish `accounts.json`, the distribution directory,
or SMS drafts through the status service or Git.

## Consensus placement

The single-host Compose deployment is not host fault tolerant. Keep it for local
testing, or migrate to one node per host using `compose.testnet.yaml`, independent
Ed25519 node keys, mutual TLS, pinned peer fingerprints, and a private network.
Do not mix the shared `SPLITCHAIN_CLUSTER_SECRET` mode with independent TLS node
identity mode. Back up each node's ledger and the exact genesis file together.

## Public status

The `/nodes` endpoint performs three internal probes and is now limited to 30
requests per source address per minute. Put a reverse proxy rate limit in front
of the service as an additional control, especially when many users share one
proxy address.

## Receiver validation

An offer now requires the receiver account to exist before any funds are locked.
This prevents authenticated clients from creating branches that can never be
accepted by an enrolled account.
