# Web enrollment for the single-host valueless testnet

The wallet page submits a 64-hex-character HMAC credential over HTTPS to an isolated intake service. The operator reviews account IDs in an authenticated dashboard; approval copies the credential into the private registry and submits a quorum-backed, zero-balance `account.register` mutation. The public status proxy never mounts credentials. No wallet is funded automatically. The operator **can impersonate these wallets** under the current shared-secret design; a future noncustodial system requires asymmetric signing keys.

## Upgrade the existing server

Run from the actual SplitChain checkout, not from `/path/to/SplitChain`. Preserve your Compose volumes, `.env.single-host`, and existing `/srv/splitchain-testnet/accounts.json`. Back up the three node volumes and that registry before upgrading. Never run `down -v`.

1. Pull the reviewed commit on `main`. Stop the round driver before recreating nodes.
2. Prepare a separate registry directory. Existing nodes still use the old file until recreated:

   ```sh
   sudo install -d -o 65532 -g 65532 -m 0700 /srv/splitchain-testnet/auth
   sudo install -o 65532 -g 65532 -m 0600 \
     /srv/splitchain-testnet/accounts.json /srv/splitchain-testnet/auth/accounts.json
   sudo install -d -o 0 -g 0 -m 0700 /srv/splitchain-testnet/enrollment-queue
   ```

3. Set these values in `.env.single-host` (keep the existing cluster secret and other settings):

   ```dotenv
   AUTH_FILE=/srv/splitchain-testnet/auth/accounts.json
   AUTH_DIRECTORY=/srv/splitchain-testnet/auth
   TESTNET_ENROLLMENT_PASSWORD=<generate-with-openssl-rand-hex-32>
   ```

   Generate the password locally with `openssl rand -hex 32`. Keep `.env.single-host` private. The existing friend-distribution tools now use `/operator/auth/accounts.json`; do not modify the obsolete registry copy after migration.

4. Review the substituted Compose configuration, recreate the three nodes and the public surfaces, then start the round driver:

   ```sh
   docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml config --quiet
   docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml stop rounds
   docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml up -d --build --force-recreate primary secondary tertiary status rpc enrollment
   docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml ps
   docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml up -d --build --force-recreate rounds
   ```

Check all three nodes are healthy and heads agree. Confirm that `GET /splitchain/create-wallet` displays **Submit for approval** and `GET /splitchain/status` continues to advance. No Nginx path change is required with the existing `/splitchain/` forwarding rule; the status service forwards only `/enroll` and `/enroll/check` to intake.

## Operator dashboard

The dashboard is bound to **127.0.0.1:8091 on the Docker host**, not exposed via Nginx. From your workstation, open a private SSH tunnel:

```sh
ssh -L 8091:127.0.0.1:8091 YOUR_SERVER_USER@YOUR_SERVER
```

Then browse to `http://127.0.0.1:8091/`. Sign in as `operator` with `TESTNET_ENROLLMENT_PASSWORD`. Review the wallet ID and click **Approve / retry**. The browser never receives credentials from the queue. If a quorum is temporarily unavailable, the request stays **approved** for retry; notify the wallet owner only when it reads **active** and the zero-balance account appears on all three node statuses.

The applicant should first save the private wallet download, click **Submit for approval**, download it again to include the status receipt, then click **Check approval status**. The receipt is a status lookup token, not the signing credential. Do not post the downloaded file or registry in public chats or GitHub.
