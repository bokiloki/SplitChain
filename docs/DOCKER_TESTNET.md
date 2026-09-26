# Three-host Docker testnet pilot

`compose.testnet.yaml` runs **one** SplitChain node per host. Use three separate
machines or VMs (Primary, Secondary, Tertiary) with separate disks and operators.
This is a **closed, valueless pilot**, not a public launch. The existing
`compose.yaml` remains a convenient single-host demo with a shared secret.

## 1. Prepare each host

Install Docker Engine with Compose v2 and clone the same reviewed Git commit on
all three hosts. Ensure the hosts can reach each other on TCP 8765 over a private
network or WireGuard, with clocks synchronized. No Kubernetes is needed.

Create the runtime directories on each host (change the paths in `.env.testnet`
if needed):

```bash
sudo install -d -o 65532 -g 65532 -m 0700 /srv/splitchain-testnet/state
sudo install -d -o 65532 -g 65532 -m 0700 /srv/splitchain-testnet/keys
cp .env.testnet.example .env.testnet
```

Edit `.env.testnet` for the **local** role, the private bind address, and both
remote peers. For example, Primary uses `secondary=wss://secondary.internal:8765`
and `tertiary=wss://tertiary.internal:8765`; Secondary uses Primary and Tertiary,
and Tertiary uses Primary and Secondary. All names must match the certificate DNS
SANs and resolve on each host. Docker Compose reads the env file when called with
`--env-file .env.testnet`. Keep `STATE_DIR` and `KEYS_DIR` absolute host paths.

## 2. Provision independent signing and TLS identities

Build the image first. On **each** host, generate the signing key inside the
mounted private directory, substituting that host's role:

```bash
docker build -t splitchain:testnet .
docker run --rm --user 65532:65532 --entrypoint scplit \
  -v /srv/splitchain-testnet/keys:/keys \
  splitchain:testnet keygen --role primary --output /keys/node.pem
```

The command prints the **public** signing key; exchange it over an authenticated
channel. Place the same `peer-keys.json` on all hosts, mapping `primary`,
`secondary`, and `tertiary` to their public key strings. Keep `node.pem` private.

Each host must also have its own `node.crt`, `node.key`, and `ca.crt` in `KEYS_DIR`.
Use a private CA to issue a distinct certificate for each node, with the node's
actual private DNS name in SAN and both server and client authentication EKUs.
Keep the CA **private key offline**. Distribute only `ca.crt`. Each host's
`peers.json` must pin the SHA-256 DER fingerprint of its **two remote** nodes:

```json
{
  "peers": [
    {"node_id": "secondary", "certificate_sha256": "64_lowercase_hex_digits", "roles": ["secondary"]},
    {"node_id": "tertiary", "certificate_sha256": "64_lowercase_hex_digits", "roles": ["tertiary"]}
  ]
}
```

This example is for Primary; change roles on the other hosts. Compute a remote
certificate fingerprint with:

```bash
openssl x509 -in remote-node.crt -outform DER | sha256sum
```

Verify the value out of band before adding it. An operational client needs its
own certificate issued by the CA and pinned in each node's `peers.json` with
`"roles": ["client"]` to query `cluster.leadership` or submit transfers.
Make all files in `KEYS_DIR` readable by UID 65532, and keep private keys readable
only by their owner. Do not place key material in the Git checkout or image.

## 3. Validate and start

On each host, inspect the complete substituted configuration and start its node:

```bash
docker compose --env-file .env.testnet -f compose.testnet.yaml config
docker compose --env-file .env.testnet -f compose.testnet.yaml up -d --build
docker compose --env-file .env.testnet -f compose.testnet.yaml ps
docker compose --env-file .env.testnet -f compose.testnet.yaml logs --tail 100 node
```

The node refuses to start if its signing role, peer registry, genesis, or TLS
materials do not agree. TCP container health checks only prove the process
listens. From an authorized client, check each host with:

```bash
scplit rpc cluster.leadership --url wss://primary.internal:8765 \
  --tls-cert client.crt --tls-key client.key --tls-ca ca.crt
```

Compare each node's leadership state and canonical ledger head. Stop Primary,
confirm Secondary and Tertiary agree on the committed prefix and observe a
certified promotion, then restart Primary and verify its rejoin. Preserve state
and logs on any disagreement. Back up each node's `state` directory and its
genesis file; **never** use `docker compose down -v` on a network whose state
you intend to retain.

The shared `configs/testnet-genesis.json` holds 21 million **valueless** test
units, including a permanently locked seven-million-unit reserve. It is mounted
read-only and must be identical on every host. This configuration has no faucet,
public onboarding, independent review, or reset governance yet; see
[PUBLIC_TESTNET_PLAN.md](PUBLIC_TESTNET_PLAN.md) before opening public access.
