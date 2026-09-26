# One-host sandbox testnet with signed transfers

This profile runs **three nodes in Docker on one server** using separate persistent
volumes, the candidate testnet genesis, and loopback-only node RPC ports.
`/status` and `/leadership` are public over HTTPS, and provisioned accounts can
submit signed transfers to the WebSocket `/rpc` endpoint. One host is a single
failure domain, so three
containers do **not** satisfy the independent-host release gate.

The nodes share an HMAC cluster secret on a private Docker network. The public
gateway permits only `offer`, `accept`, `commit`, and `cancel`. The leader and
replicas verify account signatures and replay nonces. The operator creates
accounts and distributes valueless test units manually; self-service enrollment,
an automated faucet, incident procedures, and external review remain open.
The private `rounds` service advances one round about every ten seconds, so a
committed transfer normally reaches three-round finality after about 30 seconds.

## Server installation and startup

Install Docker Engine with its Compose plugin (see the
[official Ubuntu guide](https://docs.docker.com/engine/install/ubuntu/)
if your server uses Ubuntu).
If the server currently runs containerd/nerdctl without Docker Engine, the
`docker compose` commands below need Docker installed first. Keep a stable DNS
name and a public address for the HTTPS endpoint.

```bash
git clone https://github.com/bokiloki/SplitChain.git
cd SplitChain
umask 077
cp .env.single-host.example .env.single-host
sed -i "s/^SPLITCHAIN_CLUSTER_SECRET=$/SPLITCHAIN_CLUSTER_SECRET=$(openssl rand -hex 32)/" .env.single-host
sudo install -d -m 0755 /srv/splitchain-testnet
sudo python3 scripts/init_testnet_accounts.py /srv/splitchain-testnet/accounts.json
sudo chown 65532:65532 /srv/splitchain-testnet/accounts.json
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml config --quiet
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml up -d --build
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml ps
```

The sample `PUBLIC_TESTNET_DOMAIN` is `splitchain.bokiloki.ddns.net`. Point it
to your public address and ensure your HTTPS certificate covers it. Leave the
random cluster secret in place; a changed
secret breaks replica authentication. Keep `accounts.json` private; only the
individual account secret should be delivered to its owner. The node UID 65532
must read this file. `.env.single-host` is ignored by Git and
excluded from the Docker build. Back up the three Docker volumes and the exact
genesis JSON; never run `down -v` unless deliberately resetting the testnet.

The local node RPC addresses are `ws://127.0.0.1:8765` (Primary), `:8766`
(Secondary), and `:8767` (Tertiary). The status service is locally reachable at
`http://127.0.0.1:8088/status`; the signed transfer gateway listens at
`ws://127.0.0.1:8089/rpc`. Check the three containers:

```bash
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml exec primary scplit rpc status \
  --url ws://127.0.0.1:8765
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml exec secondary scplit rpc cluster.leadership \
  --url ws://127.0.0.1:8765
curl -f http://127.0.0.1:8088/status
```

To exercise failover, stop Primary, observe the leadership view on Secondary,
then restart Primary and compare ledger heads. Keep all state and logs if nodes
disagree. With all nodes on one host, a host crash stops the whole cluster.

## HTTPS: existing Nginx or bundled Caddy

If Nginx already owns ports 80/443 on your server, add these locations **inside
the HTTPS server block for `splitchain.bokiloki.ddns.net` and reload Nginx.
If that hostname already serves another website, save its existing configuration
before replacing its root route. Keep its
existing certificate renewal and HTTP-to-HTTPS redirect. The upstream binds
to loopback only; these exact paths are the only public ones:

```nginx
location = / {
    proxy_pass http://127.0.0.1:8088/;
}
location = /.well-known/splitchain-testnet.json {
    proxy_pass http://127.0.0.1:8088/.well-known/splitchain-testnet.json;
}
location = /genesis.json {
    proxy_pass http://127.0.0.1:8088/genesis.json;
}
location = /status {
    proxy_pass http://127.0.0.1:8088/status;
}
location = /leadership {
    proxy_pass http://127.0.0.1:8088/leadership;
}
```

Then request `https://splitchain.bokiloki.ddns.net/`. For a **dedicated testnet
hostname** on a server with free ports 80/443, set `PUBLIC_TESTNET_DOMAIN` and
start the bundled Caddy profile instead:

```bash
docker compose --env-file .env.single-host --profile caddy \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml up -d --build
```

Caddy obtains and renews a public certificate for the configured hostname.
Check `https://splitchain.bokiloki.ddns.net/` and
`https://splitchain.bokiloki.ddns.net/.well-known/splitchain-testnet.json`.
Do not start the Caddy profile while
Nginx is bound to 80/443; use the existing Nginx configuration above.

When using Nginx, also add this exact WebSocket location in the same HTTPS
server block:

```nginx
location = /rpc {
    proxy_pass http://127.0.0.1:8089/rpc;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 15s;
}
```

The root URL is the **human-facing genesis entry point**. Clients discover the
machine-readable manifest at `/.well-known/splitchain-testnet.json` and verify
`/genesis.json` against the copy shipped in this repository:

```bash
scplit join-testnet --url https://splitchain.bokiloki.ddns.net/ \
  --genesis configs/testnet-genesis.json
```

Joining as a validator also requires a separately approved signing identity,
TLS peer registry, and independent-host setup; the URL alone does not grant
consensus membership. Check the [independent node guide](INDEPENDENT_NODES.md).

For a test transfer, the operator places each actor's secret alone in a private
`0600` file and gives it to that actor. Use unique, increasing nonces for each
actor. These examples use `/rpc` on the dedicated hostname:

```bash
# Run on the server with umask 077; this prints no secret to the terminal.
sudo python3 -c 'import json; print(json.load(open("/srv/splitchain-testnet/accounts.json"))["testnet_faucet"])' > faucet.secret
sudo python3 -c 'import json; print(json.load(open("/srv/splitchain-testnet/accounts.json"))["bob"])' > bob.secret
```

Distribute `bob.secret` only to Bob. Generate separate files for other actors.

To enroll another tester, provision a new account credential. The script refuses
to overwrite an account or credential. Because nodes mount a specific registry
file inode, briefly stop them and recreate their containers after each registry
update so all three read the same file:

```bash
sudo python3 scripts/add_testnet_account.py \
  /srv/splitchain-testnet/accounts.json charlie \
  /srv/splitchain-testnet/charlie.secret
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  stop primary secondary tertiary rounds
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  up -d --force-recreate primary secondary tertiary rounds
```

Give only `charlie.secret` to Charlie. The operator can then fund `charlie` by
making a signed `offer` from `testnet_faucet`; Charlie signs `accept`, and the
operator signs `commit`. No participant gets a claim on future mainnet coins.
Never send `accounts.json` to clients.

```bash
scplit rpc offer --url wss://YOUR_TESTNET_DOMAIN/rpc \
  --params '{"sender":"testnet_faucet","receiver":"bob","value":10}' \
  --actor testnet_faucet --secret-file ./faucet.secret --nonce 1
scplit rpc accept --url wss://YOUR_TESTNET_DOMAIN/rpc \
  --params '{"branch_id":"BRANCH_ID_FROM_OFFER","receiver":"bob"}' \
  --actor bob --secret-file ./bob.secret --nonce 1
scplit rpc commit --url wss://YOUR_TESTNET_DOMAIN/rpc \
  --params '{"branch_id":"BRANCH_ID_FROM_OFFER","sender":"testnet_faucet","payload":{"memo":"sandbox"}}' \
  --actor testnet_faucet --secret-file ./faucet.secret --nonce 2
```

The private round driver signs `advance` requests as `testnet_operator`. It
persists its next nonce before submission, so restarting it does not reuse an
earlier authorization. Check its logs and all three nodes' ledger heads if
finality stalls. The public gateway excludes `advance` and `cluster.sync`.

## MikroTik RB4011: WAN ports

| Use | WAN port | Router action |
| --- | --- | --- |
| Public status and signed transfers over HTTPS | TCP 443 | Forward to the server's private IP, port 443, if an HTTPS forward does not already exist. |
| TLS certificate issuance / HTTP redirect | TCP 80 | Forward to the same server, port 80, if using bundled Caddy; preserve an existing Nginx forward if using Nginx. |
| Optional remote administration over an already configured WireGuard VPN | Its configured UDP listen port | Allow inbound on the router's **input** chain; no SplitChain port forwarding. |
| Node RPC (8765–8767), status (8088), transfer gateway (8089) | None | **No WAN port forward**; all five bind to 127.0.0.1 on the server. |

If the router already forwards 80/443 to Nginx on this server, keep those rules
and use the Nginx locations above. If those ports point elsewhere, arrange an
HTTPS reverse proxy on that destination rather than adding a conflicting NAT
rule. With bundled Caddy and a server at `192.168.88.10` (replace with its
actual reserved LAN IP), example RouterOS v7 NAT rules are:

```routeros
/ip firewall nat add chain=dstnat in-interface-list=WAN protocol=tcp dst-port=80 action=dst-nat to-addresses=192.168.88.10 to-ports=80 comment="SplitChain public status HTTP"
/ip firewall nat add chain=dstnat in-interface-list=WAN protocol=tcp dst-port=443 action=dst-nat to-addresses=192.168.88.10 to-ports=443 comment="SplitChain public status HTTPS"
```

Review existing NAT and **forward** filter rules before adding these; place any
needed forwarding accept rule ahead of the WAN drop and limit it to the two
destination ports and this server. WAN traffic to the router itself uses the
**input** chain, which is relevant only if terminating WireGuard on the RB4011.
No rule should forward TCP 8765, 8766, 8767, 8088, or 8089 from WAN. Confirm your
domain resolves to your public IP and that your ISP supplies a reachable public
address (CGNAT prevents normal inbound forwarding). Test from mobile data,
outside your home LAN.

The public transfer pilot is operator-curated and experimental. See
[PUBLIC_TESTNET_PLAN.md](PUBLIC_TESTNET_PLAN.md) for the gates before an open
transaction testnet.
