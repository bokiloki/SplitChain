# Public testnet first: release plan

Status: **approved release direction**, not a claim that the network or token is ready.
SplitChain will run a public testnet with valueless test units before any mainnet coin
carrying real value. Testnet balances have no claim on a future mainnet allocation and
must never be sold or presented as redeemable.

## Current baseline (September 2026)

The repository contains an experimental ledger, simulator, TLA+ safety model, CLI,
three-node Compose reference cluster, and a quorum-certified leadership safety model.
The current live replication path still assumes Primary is the mutation leader; it
does not yet perform unattended failover or dynamic client routing. The cluster uses
a shared HMAC secret and loopback endpoints. These controls are insufficient for an
internet-facing public testnet. The broader DistOPS and TrueLies ecosystem remains
research work and is not a prerequisite for the first ledger-only testnet.

## Release gates

| Gate | Required evidence |
| --- | --- |
| T1. Protocol and issuance freeze | Versioned protocol/transaction formats, genesis specification, supply and allocation rules, upgrade policy, and accepted ADRs; cross-implementation test vectors. |
| T2. Network safety | Per-node identities and authenticated encrypted peer traffic; persisted terms and quorum certificates; leader takeover with catch-up, committed-prefix protection, fencing of old leaders, and client redirection. |
| T3. Independent operation | At least three independently operated nodes on distinct hosts; documented bootstrap, peer discovery, node rejoin, backups, restore, metrics, alerts, and reproducible deployments without Kubernetes. |
| T4. Adversarial validation | Automated partitions, crashes at every prepare/commit boundary, replay, equivocation, conflicting leaders, restart, data corruption, and resource-exhaustion tests; supply/finality invariants remain intact. |
| T5. Public testnet readiness | External review of the testnet threat model, published software and configuration, faucet with abuse limits, wallet and explorer or equivalent transaction inspection, incident contacts, and a reset policy. No monetary value or public sale. |
| M1. Mainnet readiness (later) | Independent protocol and implementation audits, resolved critical findings, longer-running public testnet results, operational key custody and incident procedures, legal classification, disclosures, and launch approvals. |

Do not open public network ports or invite real-value deposits until the relevant
gates pass. A local three-container demo is evidence for development only; it does
not satisfy T3. Testnet can reset with advance notice and must use an unmistakably
different network identifier and genesis from mainnet.

## Proposed coin allocation to specify and test

The intended **mainnet** maximum is 21,000,000 units. The proposed one-third
reserve/lock is **7,000,000 units**. This is a design decision to formalize before
testnet; the repository does not yet enforce a 21,000,000 cap or instantiate that
reserve. Recording the allocation now does not create, sell, or distribute coins.

Before T1 closes, define the exact genesis recipients and amounts (including the
remaining 14,000,000 units), vesting/unlock schedule, signing authority, reserve
spending rules, rewards, fees, decimals, and whether any additional issuance exists.
Then encode the maximum and allocation as consensus invariants and test that no
transaction, reward, restart, or upgrade can exceed the cap. Testnet must use
separate valueless units; copying balances to mainnet requires a new explicit decision.

## Immediate engineering sequence

1. Finish certificate transport, durable term state, replica catch-up, leader fencing,
   and client routing. Test a three-node failure and restart end to end.
2. Draft the issuance/genesis RFC and add supply-cap tests before a testnet genesis.
3. Replace the shared cluster secret with distinct node identities and encrypted
   authenticated transport; exercise partitions and compromised-node scenarios.
4. Package lightweight independent-node deployment, observability, faucet, and
   user-facing testnet instructions. Run a closed external pilot before public access.

The legal and economic design for a real-value launch is a separate mainnet gate.
The research whitepaper is not automatically a regulatory crypto-asset white paper.
