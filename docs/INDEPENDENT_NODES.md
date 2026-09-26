# Independent three-node pilot

This guide configures a **closed pilot** on three distinct hosts without Kubernetes.
It is not permission to expose `splitd` to the public internet or to hold real assets.
Use the public-testnet gates in [PUBLIC_TESTNET_PLAN.md](PUBLIC_TESTNET_PLAN.md).

## Identity preparation

Install Python 3.11+ and this package independently on each host. On the host assigned
each role, create its own private Ed25519 signing key:

```bash
mkdir -m 700 -p /var/lib/splitchain/keys
scplit keygen --role primary --output /var/lib/splitchain/keys/primary.pem
```

Use `secondary` and `tertiary` on their respective hosts. Keep each PEM file on its
own host; the command refuses to overwrite it. Share only the returned public-key
strings. Assemble the **same** `peer-keys.json` on all hosts:

```json
{
  "primary": "BASE64_PUBLIC_KEY_FROM_PRIMARY",
  "secondary": "BASE64_PUBLIC_KEY_FROM_SECONDARY",
  "tertiary": "BASE64_PUBLIC_KEY_FROM_TERTIARY"
}
```

Provision a private certificate authority and a distinct TLS certificate and key for
each host. Certificates must include their DNS names in Subject Alternative Name and
be valid for both server and client authentication. Distribute the CA certificate,
not its signing key. Pin both remote nodes' certificate SHA-256 DER fingerprints in
each node's TLS peer registry:

```json
{
  "peers": [
    {"node_id": "secondary", "certificate_sha256": "64_LOWERCASE_HEX_DIGITS", "roles": ["secondary"]},
    {"node_id": "tertiary", "certificate_sha256": "64_LOWERCASE_HEX_DIGITS", "roles": ["tertiary"]}
  ]
}
```

The example is the Primary's registry; each other host lists its two peers with
their actual roles. Verify each fingerprint out of band before distributing it.
Include separately authorised client certificates only when client access is needed.

## Start each role

On Primary, with its own PEM and TLS paths:

```bash
unset SPLITCHAIN_CLUSTER_SECRET
splitd --host 0.0.0.0 --port 8765 \
  --state /var/lib/splitchain/ledger.json --node-id primary --role primary \
  --genesis /etc/splitchain/testnet-genesis.json \
  --node-key /var/lib/splitchain/keys/primary.pem \
  --peer-keys /var/lib/splitchain/keys/peer-keys.json \
  --tls-cert /etc/splitchain/primary.crt --tls-key /etc/splitchain/primary.key \
  --tls-ca /etc/splitchain/ca.crt --tls-peers /etc/splitchain/peers.json \
  --peer secondary=wss://secondary.example.internal:8765 \
  --peer tertiary=wss://tertiary.example.internal:8765
```

Use corresponding paths, `--node-id`, `--role`, and peer URLs on the other two hosts.
Copy the exact same reviewed `configs/testnet-genesis.json` to each host before
first start. Its network ID and balances are a candidate for valueless testing;
do not edit it after state exists. The digest is bound into canonical history,
and a restart with another genesis is rejected. Back up the file and state together.
Allow only the three known hosts through the firewall. Use hostnames that match the
TLS certificates. All three clocks must stay reasonably synchronized because the
research election uses two-second wall-clock ticks.

Check each host with `scplit rpc cluster.leadership --url wss://HOST:8765` using a
trusted client certificate (`--tls-cert`, `--tls-key`, `--tls-ca`). A healthy closed
pilot should reproduce state across three independent disks, then survive a stopped
Primary when Secondary and Tertiary have matching clean committed histories. If
promotion stalls, preserve state files and logs for investigation; never delete
them or force a leader without resolving the committed prefix.

The old `SPLITCHAIN_CLUSTER_SECRET` mode remains for the loopback Compose demo only.
Do not mix nodes from the two identity modes in one cluster. This pilot still lacks
a controlled faucet, independent security review, and sustained adversarial
operation required for a public testnet.
