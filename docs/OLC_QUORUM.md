# OLC verifier quorum: three keys, two approvals

This phase adds a **2-of-3 signed verifier rule** for *new* fixed SHA-256 jobs.
Each job snapshots the three public keys at submission. One verifier identity
contributes at most one signed vote. The gateway accepts `quorum_verified` only
after two positive votes, marks `disputed` after two negative votes, and leaves
the job `awaiting_quorum` while a second matching vote is missing. The worker
remains outside ledger consensus; no stake, reward, or settlement is issued.

The three previously completed pilot jobs retain their historical `verified`
state and single-verifier attestation. They are **not** promoted to quorum.

## Keys on independent hosts

Keep verifier-1 on the existing testnet server. Choose **two other machines**
with Debian/Ubuntu and network access to the HTTPS testnet endpoint for
verifier-2 and verifier-3. Create each key *on the machine that will use it*.
Use a distinct non-root account and an up-to-date SplitChain checkout with its
Python virtual environment on each machine:

```bash
# On verifier-2 host:
cd ~/SplitChain && git pull --ff-only && .venv/bin/pip install -e .
.venv/bin/python scripts/create_olc_verifier_key.py verifier-2

# On verifier-3 host (different machine):
cd ~/SplitChain && git pull --ff-only && .venv/bin/pip install -e .
.venv/bin/python scripts/create_olc_verifier_key.py verifier-3
```

The commands print **only public keys**. Record each 64-character public key;
never paste or copy the private PEM files to the server or chat.

## Register the three-key roster

On the **testnet server**, replace the placeholders with the public keys:

```bash
cd ~/SplitChain
git pull --ff-only
sudo python3 scripts/upgrade_olc_quorum.py /srv/splitchain-testnet/olc \
  --verifier-2-public PUBLIC_KEY_FROM_HOST_2 \
  --verifier-3-public PUBLIC_KEY_FROM_HOST_3
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  up -d --build --force-recreate status olc-gateway olc-verifier
```

The upgrade refuses to overwrite a registered roster, retains the old
`credentials.json.pre-quorum` in a root-only backup, and creates two root-only
token files. The public gateway receives **no new private keys**. Recreating
the gateway remounts the updated credentials file; the existing verifier-1
container uses its old key and token under the new `verifier-1` identity.

## Deliver the two tokens over trusted LAN SSH

On the testnet server, replace the usernames and LAN addresses with the
two chosen verifier accounts and hosts. Check each SSH host key:

```bash
sudo cat /srv/splitchain-testnet/olc/verifier-2-token | \
  ssh USER2@HOST2 'umask 077; mkdir -p ~/.config/olc; cat > ~/.config/olc/verifier-2-token'
sudo cat /srv/splitchain-testnet/olc/verifier-3-token | \
  ssh USER3@HOST3 'umask 077; mkdir -p ~/.config/olc; cat > ~/.config/olc/verifier-3-token'
```

On each verifier host, install the corresponding user service:

```bash
mkdir -p ~/.config/systemd/user
cp ~/SplitChain/olc/olc-verifier@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now olc-verifier@verifier-2.service  # host 2 only
sudo loginctl enable-linger "$USER"
```

Use `olc-verifier@verifier-3.service` on host 3. Both connect **outbound over
HTTPS**; no verifier SSH or API port needs WAN forwarding. Check their journals
with `journalctl --user -u olc-verifier@verifier-2.service -n 30 --no-pager`
(substitute 3 on host 3).

## Prove the rule

Submit one new job from the testnet server:

```bash
sudo python3 scripts/olc_operator.py submit
sudo python3 scripts/olc_operator.py jobs
curl -fsS https://bokiloki.ddns.net/splitchain/receipts
```

The job first becomes `awaiting_quorum`. With only verifier-1 running it stays
there. After either external verifier votes positively it becomes
`quorum_verified`; the third verifier can be offline. The public Jobs page
shows both named signatures. Earlier `verified` jobs remain distinguishable.

Use `scripts/verify_olc_receipts.py` on a separate machine to validate the
public signatures. For stronger pinning, record the roster SHA-256 fingerprint
and pass `--roster-sha256` on subsequent checks. The roster fingerprint comes
from `sha256(canonical_json(verifier_keys))` for the three public keys in a
receipt. Trust in which hosts hold those keys still relies on the operator's
provisioning records, not on the receipt alone.
