# One-host valueless testnet with public status

This profile runs **three nodes in Docker on one server** using separate persistent
volumes, the candidate testnet genesis, and loopback-only RPC ports. A small HTTP
service makes `/status` and `/leadership` readable to the public over HTTPS. It
rejects transaction requests. One host is a single failure domain, so three
containers do **not** satisfy the independent-host release gate.

The one-host nodes use the shared HMAC demo mode on a private Docker network.
Keep the repository's transaction RPC ports off the public network. The public
status service does not issue units or accept transfers. Public participation
requires account authentication, a limited faucet, incident operations, and
independent review before the transaction interface can be opened.

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
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml config --quiet
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml up -d --build
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml ps
```

Edit `PUBLIC_TESTNET_DOMAIN` in `.env.single-host` to an actual DNS name before
starting bundled Caddy. Leave the random cluster secret in place; a changed
secret breaks replica authentication. `.env.single-host` is ignored by Git and
excluded from the Docker build. Back up the three Docker volumes and the exact
genesis JSON; never run `down -v` unless deliberately resetting the testnet.

The local node RPC addresses are `ws://127.0.0.1:8765` (Primary), `:8766`
(Secondary), and `:8767` (Tertiary). The status service is locally reachable at
`http://127.0.0.1:8088/status`. Check the three containers:

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
the existing HTTPS server block** for your domain and reload Nginx. Keep its
existing certificate renewal and HTTP-to-HTTPS redirect. The upstream binds
to loopback only; these exact paths are the only public ones:

```nginx
location = /testnet/status {
    proxy_pass http://127.0.0.1:8088/status;
}
location = /testnet/leadership {
    proxy_pass http://127.0.0.1:8088/leadership;
}
```

Then request `https://YOUR_DOMAIN/testnet/status`. For a **dedicated testnet
hostname** on a server with free ports 80/443, set `PUBLIC_TESTNET_DOMAIN` and
start the bundled Caddy profile instead:

```bash
docker compose --env-file .env.single-host --profile caddy \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml up -d --build
```

Caddy obtains and renews a public certificate for the configured hostname.
Check `https://YOUR_TESTNET_DOMAIN/status`. Do not start the Caddy profile while
Nginx is bound to 80/443; use the existing Nginx configuration above.

## MikroTik RB4011: WAN ports

| Use | WAN port | Router action |
| --- | --- | --- |
| Public read-only HTTPS | TCP 443 | Forward to the server's private IP, port 443, if an HTTPS forward does not already exist. |
| TLS certificate issuance / HTTP redirect | TCP 80 | Forward to the same server, port 80, if using bundled Caddy; preserve an existing Nginx forward if using Nginx. |
| Optional remote administration over an already configured WireGuard VPN | Its configured UDP listen port | Allow inbound on the router's **input** chain; no SplitChain port forwarding. |
| SplitChain RPC (8765–8767), status upstream (8088) | None | **No WAN port forward**; all four bind to 127.0.0.1 on the server. |

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
No rule should forward TCP 8765, 8766, 8767, or 8088 from WAN. Confirm your
domain resolves to your public IP and that your ISP supplies a reachable public
address (CGNAT prevents normal inbound forwarding). Test from mobile data,
outside your home LAN.

The public endpoint is **informational only**. See
[PUBLIC_TESTNET_PLAN.md](PUBLIC_TESTNET_PLAN.md) for the gates before an open
transaction testnet.
