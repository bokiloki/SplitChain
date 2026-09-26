# Six-validator admission plan for the existing testnet

**Status: candidate identity preparation only. Do not connect candidate nodes to the live quorum.** The running node, leader failover, peer registry and replication code currently require precisely `primary`, `secondary`, and `tertiary`; mutations use a two-of-three quorum. The candidate setup tool does not grant votes or open network ports.

The proposed membership is three current validators plus `colleague-primary`, `colleague-secondary`, and `colleague-tertiary`. A six-validator two-thirds quorum is **four**, so a 3–3 partition cannot finalize new mutations. Three instances on one colleague-owned host share a fault domain and are not three independent machines. This research implementation has not established Byzantine fault tolerance against a participant controlling half the validators.

## What the colleague can prepare now

On the colleague's own host, clone and review SplitChain. Verify the pinned genesis with `scplit join-testnet --url https://bokiloki.ddns.net/splitchain/ --genesis configs/testnet-genesis.json`. Then generate candidate node identities locally:

```sh
python3 -m splitchain.validator_candidate prepare \
  --genesis configs/testnet-genesis.json --output "$HOME/splitchain-validator-keys"
python3 -m splitchain.validator_candidate verify \
  --genesis configs/testnet-genesis.json \
  --invite "$HOME/splitchain-validator-keys/public-invite.json"
```

Share **only** `public-invite.json` with the operator over an authenticated channel; keep the three `.pem` private keys private. This is a proposal for review, not a valid validator configuration. Separately, the colleague can create a test wallet through the web enrollment page, obtain operator approval and receive valueless test units. Their wallet can exercise public signed transfers today without validator rights.

## Protocol work required before voting admission

1. Replace the fixed three-name identity, peer, leader, and replica lists with a signed membership epoch committed at a specific ledger position. Historical leadership certificates must retain the membership and quorum rules under which they were signed.
2. Introduce a **joint transition**: for each transition mutation, require both the old quorum (two of the original three) and the new quorum (four of six) until every validator has the same committed epoch and state. A simple switch permits disjoint old/new quorums to finalize conflicting entries.
3. Remove the shared `SPLITCHAIN_CLUSTER_SECRET` before an independently operated validator votes. Use per-node Ed25519 signatures and mutually authenticated transport, with independent public-key pinning and renewal. Never send `/srv/splitchain-testnet/auth/accounts.json`, Docker volumes, private node keys, or the round driver credential to the colleague.
4. The current HMAC wallet registry is needed by each voting node to verify wallet requests. Replace it with ledger-pinned **public** wallet keys (plus a controlled migration for the existing 100 wallets) or a quorum-verified authorization proof that does not reveal wallet secrets. A candidate is not safe to promote until all six validate transactions without sharing those secrets.
5. Bootstrap new nodes from an authenticated checkpoint and replay of the committed log. Verify genesis, epoch, canonical history, finalization state, leadership certificates, and replay nonces against at least an old quorum. Exercise state divergence, 3–3 partitions, stale terms, equivocation and partial join in tests.
6. Introduce an operator-approved admission transaction, a rollback/abort path before promotion, observability for every validator, and a documented coordinated host rollout. Do not reuse the one-host Compose overlay on your colleague's host or expose port 8765 directly to the internet.

**Funding** is separate from membership: after the colleague's wallet becomes active, transfer a chosen test-unit amount from the faucet or another funded account. The operator must consider stake equal to transfer value when funding outgoing offers. Wallet funding conveys no validator authority.
