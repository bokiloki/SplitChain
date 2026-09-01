----------------------------- MODULE SplitChain -----------------------------
EXTENDS Naturals, FiniteSets

CONSTANTS Accounts, MaxValue

States == {"none", "offered", "accepted", "committed", "final", "cancelled", "expired"}

VARIABLES balance, locked, state, sender, receiver, value, stake, round, commitRound,
          canonicalHeight, originHeight
vars == <<balance, locked, state, sender, receiver, value, stake, round, commitRound,
          canonicalHeight, originHeight>>

Init == /\ balance \in [Accounts -> 0..MaxValue]
        /\ locked = [a \in Accounts |-> 0]
        /\ state = "none"
        /\ sender \in Accounts /\ receiver \in Accounts
        /\ value = 0 /\ stake = 0 /\ round = 0 /\ commitRound = 0
        /\ canonicalHeight = 0 /\ originHeight = 0

Offer(s, r, v) ==
  /\ state = "none" /\ s # r /\ v > 0 /\ balance[s] >= 2 * v
  /\ state' = "offered" /\ sender' = s /\ receiver' = r
  /\ value' = v /\ stake' = v /\ locked' = [locked EXCEPT ![s] = @ + 2 * v]
  /\ originHeight' = canonicalHeight
  /\ UNCHANGED <<balance, round, commitRound, canonicalHeight>>

Accept == /\ state = "offered" /\ state' = "accepted"
          /\ UNCHANGED <<balance, locked, sender, receiver, value, stake, round, commitRound,
                          canonicalHeight, originHeight>>

Commit == /\ state = "accepted" /\ state' = "committed" /\ commitRound' = round
          /\ UNCHANGED <<balance, locked, sender, receiver, value, stake, round,
                          canonicalHeight, originHeight>>

Tick == /\ state \in {"offered", "accepted", "committed"} /\ round' = round + 1
        /\ IF state = "committed" /\ round' - commitRound >= 3
              THEN /\ state' = "final"
                   /\ balance' = [balance EXCEPT ![sender] = @ - value, ![receiver] = @ + value]
                   /\ locked' = [locked EXCEPT ![sender] = @ - 2 * value]
                   /\ canonicalHeight' = canonicalHeight + 1
              ELSE /\ UNCHANGED <<state, balance, locked, canonicalHeight>>
        /\ UNCHANGED <<sender, receiver, value, stake, commitRound, originHeight>>

Cancel == /\ state \in {"offered", "accepted"} /\ state' = "cancelled"
          /\ locked' = [locked EXCEPT ![sender] = @ - 2 * value]
          /\ UNCHANGED <<balance, sender, receiver, value, stake, round, commitRound,
                          canonicalHeight, originHeight>>

Next == (\E s, r \in Accounts, v \in 1..MaxValue: Offer(s, r, v)) \/ Accept \/ Commit \/ Tick \/ Cancel

TypeOK == /\ state \in States /\ value \in Nat /\ stake \in Nat /\ round \in Nat
          /\ canonicalHeight \in Nat /\ originHeight \in Nat
EqualStake == state = "none" \/ stake = value
NonNegative == \A a \in Accounts: balance[a] >= 0 /\ locked[a] >= 0 /\ locked[a] <= balance[a]
RecognizedOrigin == state = "none" \/ originHeight <= canonicalHeight
Safety == TypeOK /\ EqualStake /\ NonNegative /\ RecognizedOrigin

Spec == Init /\ [][Next]_vars
=============================================================================
