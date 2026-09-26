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

The sample `PUBLIC_TESTNET_DOMAIN` is `bokiloki.ddns.net`. Point it
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

## Public HTTPS path on the existing Nginx host

The public entry point is **https://bokiloki.ddns.net/splitchain/**. The
SplitChain `status` and `rpc` services join the external Docker network
`public-proxy`. Attach the existing Nginx service to the same network; create
it once with `docker network create public-proxy` if it does not exist.
Keep the existing `bokiloki.ddns.net` TLS certificate, HTTPS redirect, and
other Nginx locations. Add these locations inside its existing HTTPS server
block (before other broad `/splitchain/` rules):

```nginx
location = /splitchain {
    return 301 /splitchain/;
}
location = /splitchain/rpc {
    proxy_pass http://splitchain-rpc:8089/rpc;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-SplitChain-Client-IP $remote_addr;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 20s;
}
location /splitchain/ {
    proxy_pass http://splitchain-status:8080/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto https;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
}
```

The trailing slash in the status `proxy_pass` removes the `/splitchain/`
prefix. Keep the RPC location separate because it must upgrade to WebSocket.
Reload the existing Nginx container after validating its configuration.
The nodes and status/RPC loopback bindings need **no WAN port forwarding**.

```bash
curl -fsS https://bokiloki.ddns.net/splitchain/
curl -fsS https://bokiloki.ddns.net/splitchain/downloads
curl -fsS https://bokiloki.ddns.net/splitchain/wallet.js
curl -fsS https://bokiloki.ddns.net/splitchain/.well-known/splitchain-testnet.json
curl -fsS https://bokiloki.ddns.net/splitchain/status
scplit join-testnet --url https://bokiloki.ddns.net/splitchain/ \
  --genesis configs/testnet-genesis.json
```

The browser wallet and Android APK link appear on the Downloads page. The
browser wallet uses `wss://bokiloki.ddns.net/splitchain/rpc`. The public
status endpoint is read-only; only signed `offer`, `accept`, `commit`, and
`cancel` requests pass the RPC gateway. Opening the bootstrap page alone
does not enroll a consensus validator.

## Enroll and fund a tester

To issue **100 ready-funded friend wallets at 100 test units each**, use the
[operator batch distributor](FRIENDS_DISTRIBUTION.md); it keeps private SMS
drafts on the server and resumes without duplicating transfers.


The operator must first issue one private credential per participant. Use the
provisioning script that writes a mode-0600 secret file, **never** send
`accounts.json` to anyone. Because the registry is bind mounted by inode,
stop and recreate the three nodes and the round driver after enrollment:

```bash
sudo python3 scripts/add_testnet_account.py \
  /srv/splitchain-testnet/accounts.json tester_alice \
  /srv/splitchain-testnet/tester_alice.secret
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  stop primary secondary tertiary rounds
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  up -d --force-recreate primary secondary tertiary rounds
```

Privately give Alice her account ID `tester_alice` and the contents of **only**
`tester_alice.secret`. She enters them into the Android or browser wallet,
then shares her account ID. The operator funds her from the faucet with a
signed offer, Alice refreshes and taps **Accept**, and the operator refreshes
and taps **Commit**. The round driver advances three finality rounds before
her received balance becomes spendable. Example operator commands (keep
`faucet.secret` private):

```bash
umask 077
sudo python3 -c 'import json; print(json.load(open("/srv/splitchain-testnet/accounts.json"))["testnet_faucet"])' > faucet.secret
FAUCET_NONCE=$(date +%s%3N)
scplit rpc offer --url wss://bokiloki.ddns.net/splitchain/rpc \
  --params '{"sender":"testnet_faucet","receiver":"tester_alice","value":10}' \
  --actor testnet_faucet --secret-file ./faucet.secret --nonce "$FAUCET_NONCE"
# Copy branch_id from the offer result. Wait until Alice accepts it.
scplit rpc commit --url wss://bokiloki.ddns.net/splitchain/rpc \
  --params '{"branch_id":"BRANCH_ID_FROM_OFFER","sender":"testnet_faucet","payload":{}}' \
  --actor testnet_faucet --secret-file ./faucet.secret --nonce "$((FAUCET_NONCE + 1))"
```

Use a nonce **greater** than any previously accepted nonce for this account;
choose a current millisecond timestamp if uncertain. The app reserves one
before each request. The round driver persists its own nonce across restarts.
A separate phone does not become a consensus validator by using the wallet.

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
