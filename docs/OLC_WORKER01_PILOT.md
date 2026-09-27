# OLC Worker01 pilot: fixed-job path

This pilot connects one Debian worker to the existing HTTPS testnet site. The
worker initiates all connections. The public `/splitchain/workers` endpoint shows
online status (heartbeat newer than 45 seconds) and reported CPU/RAM. An operator
can queue one hardcoded SHA-256 test job. The worker executes it in a rootless
Podman container and sends the digest back. The gateway independently computes
the expected SHA-256 and records `verified` or `rejected` in its own persistent
volume. **This is coordinator verification, not TrueLies distributed quorum,
SplitChain settlement, consensus membership, or general workload execution.**

An optional second verifier runs as a separate process with its own Ed25519 key.
It independently hashes the fixed input and signs an attestation bound to the
job ID, worker ID, digest, and acceptance decision. This is one verifier on the
same physical server as the gateway; it is **not** an independent-host quorum.
The existing coordinator decision is retained, and a disagreeing attestation
marks the job `disputed`.

The two credentials are distinct. Never share the operator token with Worker01,
publish either token, or open SSH/Podman to the Internet.

## On the existing testnet server

From the server's SplitChain checkout, using the same Compose configuration as
the existing public testnet:

```bash
git pull --ff-only
sudo python3 scripts/init_olc_credentials.py /srv/splitchain-testnet/olc
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  up -d --build status olc-gateway
curl -fsS https://bokiloki.ddns.net/splitchain/workers
```

If your deployment uses a different absolute credentials path, set
`OLC_CREDENTIALS_FILE` in `.env.single-host`. The provisioning script refuses to
overwrite credentials. Back up `/srv/splitchain-testnet/olc/credentials.json`
and the `olc-gateway-data` volume together; losing the volume loses the pilot
job history. Do not run `docker compose down -v` on the testnet.

Send **only the worker token** from the server over the already tested LAN SSH
connection to `bokiloki@192.168.88.136`:

```bash
sudo cat /srv/splitchain-testnet/olc/worker-token | \
  ssh bokiloki@192.168.88.136 \
    'umask 077; mkdir -p ~/.config/olc; cat > ~/.config/olc/worker-token'
```

Check the SSH host key before accepting it. If the testnet server cannot reach
Worker01 on the LAN, transfer the token through another trusted local machine;
do not paste it into chat.

## On Worker01

The Debian 13 image and rootless Podman from `olc/smoke-job.sh` should already
be present. From the worker's SSH session:

```bash
cd ~/SplitChain
git pull --ff-only
.venv/bin/pip install -e .
test "$(stat -c %a ~/.config/olc/worker-token)" = 600
.venv/bin/python -m splitchain.olc_worker \
  --genesis "$HOME/SplitChain/configs/testnet-genesis.json" \
  --token-file "$HOME/.config/olc/worker-token" --once
```

The once run verifies the pinned HTTPS genesis, sends one heartbeat and polls
for a job. Check `https://bokiloki.ddns.net/splitchain/explore/workers` for an
online worker. To keep it running after logout:

```bash
mkdir -p ~/.config/systemd/user
cp olc/olc-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now olc-worker.service
sudo loginctl enable-linger bokiloki
systemctl --user status olc-worker.service --no-pager
```

The unit assumes the `bokiloki` account and `~/SplitChain` checkout. It runs
the worker as that account, with no SSH or Podman TCP port exposed.

## Submit and inspect the first remote job

On the **testnet server** (not Worker01):

```bash
sudo python3 scripts/olc_operator.py submit
sudo python3 scripts/olc_operator.py jobs
```

Within a polling interval the job should read `verified`, with
`verification: coordinator-sha256` in the worker's journal. Inspect it on
Worker01 with `journalctl --user -u olc-worker.service -n 30 --no-pager`.
Only the fixed SHA-256 job is accepted by this pilot; unrecognized job kinds
and mismatched digests are rejected. No token or untrusted job command is
accepted from the public explorer.

## Add the first signed verifier

After the pilot has run successfully, on the **testnet server**:

```bash
git pull --ff-only
sudo python3 scripts/init_olc_verifier.py /srv/splitchain-testnet/olc
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  up -d --build --force-recreate olc-gateway olc-verifier
sudo python3 scripts/olc_operator.py jobs
```

The credential script refuses to overwrite an existing verifier and keeps a
root-only `credentials.json.pre-verifier` backup. Keep the verifier private key
and token on the server; neither is copied to Worker01. The gateway and verifier
containers communicate only on their private Docker network. The gateway
container is recreated so its bind mount reads the updated credentials file.
Within one poll interval each previously verified job receives an `attestation`
object with the signed statement. A new job should likewise gain an attestation
after Worker01 returns its result. This is an auditable first verifier, not a
TrueLies 2/3 quorum; two independently hosted verifiers and quorum rules remain
to be implemented before settlement.
