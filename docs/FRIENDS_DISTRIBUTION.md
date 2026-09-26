# Fund 100 friends with 100 valueless test units each

This operator-only procedure funds **100 newly created accounts × 100 test units =
10,000 test units** from `testnet_faucet`. The operator creates all account
credentials, signs the receiver's initial acceptance while still holding each
credential, and commits each transfer. Three rounds later, every friend has
100 spendable sandbox units. The recipient can then send further transactions
using the Android or browser wallet. These units have no monetary value.

No SMS is sent automatically. The server retains **100 individual SMS drafts**
containing the account ID, credential, and wallet URL, each mode `0600`, in a
mode `0700` directory. Read one over your authenticated SSH connection from
your phone and send it privately to its intended friend. Never put the drafts
or the registry in Git, a web directory, or a group chat.

## On the SplitChain server

Pull the latest `main` in your existing SplitChain checkout, confirm that
`.env.single-host` and `/srv/splitchain-testnet/accounts.json` already exist,
and that the node registry is mounted from that path. If your registry lives
elsewhere, set `TESTNET_OPERATOR_DIR` in `.env.single-host` to its **parent
directory**. The Compose `distributor` service is operator-only; it exposes
no ports and is never started by the normal `up -d` command.

```bash
git pull origin main
sudo install -d -m 0700 /srv/splitchain-testnet/distribution

docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  --profile operator build distributor

docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  --profile operator run --rm distributor prepare
```

Preparation creates `friend001` through `friend100`. It refuses to replace
existing accounts or an unrelated batch directory; rerunning the same
`prepare` command can finish a preparation interrupted before the registry was
updated. It **does not spend test units**. Inspect the output before funding:

```bash
sudo ls -l /srv/splitchain-testnet/distribution/
sudo cat /srv/splitchain-testnet/distribution/plan.json
```

The nodes retain the old account registry until restarted. Recreate all three
nodes and the round driver to load the new credentials:

```bash
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  stop primary secondary tertiary rounds

docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  up -d --force-recreate primary secondary tertiary rounds
```

Once those services are healthy, fund and wait for finality:

```bash
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  --profile operator run --rm distributor fund --wait-finality
```

The program shows progress and finishes with `Verified 100 final balances ×
100 = 10000 valueless units.` Check it again without spending anything:

```bash
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  --profile operator run --rm distributor report
```

### If the first offer reports a 2/3 quorum failure

Do **not** rerun `prepare`, remove the node volumes, or send a second manual
offer. The distributor now checks that the certified leader and a replica
agree on the full ledger digest and replication position before funding. Run
this read-only diagnostic after updating the distributor image:

```bash
docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  --profile operator run --build --rm distributor diagnose

docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml ps

docker compose --env-file .env.single-host \
  -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml \
  logs --tail=100 primary secondary tertiary rounds
```

Compare the node names, `leader`, `term`, `replication_nonce`, and
`replication_digest` in the diagnostic output. At least one replica must
match the certified leader. An `invalid or incomplete replication history`
error or missing node needs investigation before attempting another payment.
The output contains no wallet credentials and is suitable for sharing when
asking for help. Keep the account registry and SMS drafts private.

If funding stops, rerun the **same** `fund --wait-finality` command. It reads
the ledger and a private `/srv/splitchain-testnet/distribution/state.json`
journal, so finalized and committed payments are not repeated. A lost RPC
response may leave an uncertain operation; if the ledger cannot confirm that
operation, the program stops rather than paying twice. Inspect all three
node states before manually intervening. If you have used the faucet
credential with a nonce higher than the current millisecond timestamp, supply
`fund --faucet-next-nonce NUMBER`, where NUMBER exceeds its last accepted
nonce. Do not decrease it.

## Send one wallet by SMS from your phone

The account ID and private credential are together in one SMS draft. For
example, from an SSH client on your phone, connecting through your normal
private administration route:

```bash
ssh -t YOUR_SERVER_USER@YOUR_SERVER 'sudo cat /srv/splitchain-testnet/distribution/friend001.sms.txt'
```

Copy the message into an SMS to the person you selected for `friend001`.
Repeat with `friend002` through `friend100`, using each draft **once** for its
intended person. The recipient can install the APK from
[the testnet Downloads page](https://bokiloki.ddns.net/splitchain/downloads)
or use the browser wallet there. They enter the Wallet ID and Credential from
their message, then refresh to see 100 test units after finality. They should
keep their credential private; whoever knows it can spend that wallet's test
units. SMS is suitable only for this valueless sandbox pilot.

A fresh run with a different prefix or amount needs a different private
output directory and a deliberate new distribution plan. It must not reuse
this batch's `state.json` or credentials.
