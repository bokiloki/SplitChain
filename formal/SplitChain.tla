----------------------------- MODULE SplitChain -----------------------------
EXTENDS Naturals, FiniteSets

CONSTANTS Accounts, MaxValue

States == {"none", "offered", "accepted", "committed", "final", "cancelled", "expired"}

VARIABLES balance, locked, state, sender, receiver, value, stake, round, commitRound
vars == <<balance, locked, state, sender, receiver, value, stake, round, commitRound>>

Init == /\ balance \in [Accounts -> 0..MaxValue]
        /\ locked = [a \in Accounts |-> 0]
        /\ state = "none"
        /\ sender \in Accounts /\ receiver \in Accounts
        /\ value = 0 /\ stake = 0 /\ round = 0 /\ commitRound = 0

Offer(s, r, v) ==
  /\ state = "none" /\ s # r /\ v > 0 /\ balance[s] >= 2 * v
  /\ state' = "offered" /\ sender' = s /\ receiver' = r
  /\ value' = v /\ stake' = v /\ locked' = [locked EXCEPT ![s] = @ + 2 * v]
  /\ UNCHANGED <<balance, round, commitRound>>

Accept == /\ state = "offered" /\ state' = "accepted"
          /\ UNCHANGED <<balance, locked, sender, receiver, value, stake, round, commitRound>>

Commit == /\ state = "accepted" /\ state' = "committed" /\ commitRound' = round
          /\ UNCHANGED <<balance, locked, sender, receiver, value, stake, round>>

Tick == /\ state \in {"offered", "accepted", "committed"} /\ round' = round + 1
        /\ IF state = "committed" /\ round' - commitRound >= 3
              THEN /\ state' = "final"
                   /\ balance' = [balance EXCEPT ![sender] = @ - value, ![receiver] = @ + value]
                   /\ locked' = [locked EXCEPT ![sender] = @ - 2 * value]
              ELSE /\ UNCHANGED <<state, balance, locked>>
        /\ UNCHANGED <<sender, receiver, value, stake, commitRound>>

Cancel == /\ state \in {"offered", "accepted"} /\ state' = "cancelled"
          /\ locked' = [locked EXCEPT ![sender] = @ - 2 * value]
          /\ UNCHANGED <<balance, sender, receiver, value, stake, round, commitRound>>

Next == (\E s, r \in Accounts, v \in 1..MaxValue: Offer(s, r, v)) \/ Accept \/ Commit \/ Tick \/ Cancel

TypeOK == /\ state \in States /\ value \in Nat /\ stake \in Nat /\ round \in Nat
EqualStake == state = "none" \/ stake = value
NonNegative == \A a \in Accounts: balance[a] >= 0 /\ locked[a] >= 0 /\ locked[a] <= balance[a]
Safety == TypeOK /\ EqualStake /\ NonNegative

Spec == Init /\ [][Next]_vars
=============================================================================

