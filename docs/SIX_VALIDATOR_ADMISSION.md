# Six-validator admission plan for the existing testnet

**Status: candidate identity preparation only. Do not connect candidate nodes to the live quorum.** The running node, leader failover, peer registry and replication code currently require precisely `primary`, `secondary`, and `tertiary`; mutations use a two-of-three quorum. The candidate setup tool does not grant votes or open network ports.

The operator's intended rule is **stake-weighted validation**, not one vote per node: a validator may approve a transaction only if its committed validator stake is at least that transaction's value. The operator keeps the coins; a colleague's three candidate nodes have no assigned stake and must have zero voting authority. A transfer's temporary sender stake is not validator stake. The current code does **not** implement validator stake or stake-weighted quorums; it counts two of the three named roles. Candidate nodes cannot yet join live replication. Three instances on one colleague-owned host share a fault domain and are not three independent machines.

## Proposed stake decision rule

The pure `StakeMembership` policy below is testable now but has **not** been integrated with live consensus. For each committed epoch, bind each validator public key to exactly one nonnegative allocation backed by locked coins controlled by the operator. The sum of allocations must not exceed the locked backing. Candidate identities start at zero. For a transaction of value `v > 0`, accept a signature from validator `i` only if `allocation[i] >= v`; sum the eligible allocations of **distinct verified signers** and require `floor(2 * total_epoch_stake / 3) + 1`. Zero-weight nodes never lower the denominator. A large transaction for which eligible stake cannot reach that threshold must stop safely. A vote signs the epoch digest, transaction digest and value; it cannot be reused in another epoch or for another amount. Apply the same epoch rule to elections and rounds with a separately specified amount or election eligibility rule; do not treat a mutation vote as an election vote.

The backing coins remain owned by the operator and locked while assigned; delegated voting authority gives the colleague control of their own validator signatures, not withdrawal rights. How to commit backing deposits, lock and unlock them, handle slash or challenge evidence, and migrate the existing wallet registry must be implemented and reviewed before activation. Independent identities operated by one person are still one fault domain. A sole stake owner can authorize enough stake to override the other nodes: stake weighting alone cannot protect against compromise or malicious use of that owner's keys.

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

1. Define a committed validator-stake registry distinct from wallet balances and temporary sender stake. Specify how the operator assigns stake to a validator identity **without transferring coin ownership**, how it is locked, withdrawn, and prevented from being reused across multiple identities. Pin the stake snapshot and membership epoch at each decision; reject an approval when `validator_stake[voter] < transaction_value`. Zero-stake candidates have no vote.
2. Specify the stake-weight quorum threshold and its failure model before admission. Count each validator identity's assigned stake at most once, reject conflicting signatures and stale stake snapshots, and require old and new stake quorums during an epoch transition. The existing `Membership` helper is node-count-based research scaffolding and must not be wired into production as a substitute for this rule. Test partitions, multiple identities controlled by one owner, and transactions larger than an individual validator's stake.
3. Remove the shared `SPLITCHAIN_CLUSTER_SECRET` before an independently operated validator votes. Use per-node Ed25519 signatures and mutually authenticated transport, with independent public-key pinning and renewal. Never send `/srv/splitchain-testnet/auth/accounts.json`, Docker volumes, private node keys, or the round driver credential to the colleague.
4. The current HMAC wallet registry is needed by each voting node to verify wallet requests. Replace it with ledger-pinned **public** wallet keys (plus a controlled migration for the existing 100 wallets) or a quorum-verified authorization proof that does not reveal wallet secrets. A candidate is not safe to promote until all six validate transactions without sharing those secrets.
5. Bootstrap new nodes from an authenticated checkpoint and replay of the committed log. Verify genesis, stake snapshot, epoch, canonical history, finalization state, leadership certificates, and replay nonces against an authorized quorum. Exercise state divergence, partitions, stale terms, equivocation and partial join in tests.
6. Introduce an operator-approved admission transaction, a rollback/abort path before promotion, observability for every validator, and a documented coordinated host rollout. Do not reuse the one-host Compose overlay on your colleague's host or expose port 8765 directly to the internet.

**Funding** is separate from membership: a colleague may receive test units for wallet transfers without validator authority. The operator retains ownership of validator coins. A transaction cannot receive a validator approval from a node whose assigned validator stake is smaller than its value once the stake-weighted protocol exists.
