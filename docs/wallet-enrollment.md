# Operator-approved testnet wallet enrollment

This is a valueless pilot. The browser page at `/splitchain/create-wallet` generates a wallet ID and HMAC credential locally; it **does not upload** either. There is no public self-registration RPC. The operator must receive the complete private JSON file through a trusted private channel. Anyone who sees its credential can sign requests as that wallet; do not post it in chat, GitHub, logs, or a public web directory.

## Wallet owner

Open `https://bokiloki.ddns.net/splitchain/create-wallet` in a modern browser. Generate a wallet, download the JSON file, and store it securely. Privately deliver a copy to the operator. Until the operator approves and activates it, the wallet has no on-chain account and cannot receive test units. After activation, import the Wallet ID and Credential into the browser wallet or Android app. This is a shared HMAC credential, **not** a noncustodial private key: the operator must hold a copy in the server authentication registry.

## Operator (one-host Compose deployment)

Use the real checkout directory and keep each request in a private directory on the server. For example, after safely transferring `sc123456.wallet.json` to `/srv/splitchain-testnet/enrollment/`, ensure the directory is mode 0700 and the request file mode 0600. Substitute the actual account file name; do not use a wildcard. Back up `/srv/splitchain-testnet/accounts.json` and the three node volumes before changing the cluster.

From the checkout directory, with the same `.env.single-host` and Compose files used to run the network:

```sh
docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml --profile operator run --rm --entrypoint python distributor -m splitchain.enrollment approve --request /operator/enrollment/sc123456.wallet.json
```

Verify that the command prints `Approved sc123456 ...`; it will not print the credential. Next stop the round driver, recreate **all three nodes** so each reloads the private registry, and wait for health:

```sh
docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml stop rounds
docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml up -d --build --force-recreate primary secondary tertiary status
docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml ps
```

Only after all three nodes are healthy, submit the registration mutation:

```sh
docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml --profile operator run --rm --entrypoint python distributor -m splitchain.enrollment activate --request /operator/enrollment/sc123456.wallet.json
curl -fsS https://bokiloki.ddns.net/splitchain/status
docker compose --env-file .env.single-host -f compose.testnet.single-host.yaml -f compose.testnet.public.yaml start rounds
```

Check that the public status `result.balances.sc123456` is `0` and all node heads agree before notifying the owner. If activation reports an uncertain result, inspect status and leadership before retrying; the command checks for an already-active account. Registration does not fund the wallet; funding remains a separate operator transaction. Do not edit node ledger files or remove Docker volumes.
