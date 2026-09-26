------------------------ MODULE SplitChainMultiBranch ------------------------
EXTENDS Naturals, FiniteSets

CONSTANTS Accounts, Branches, MaxValue, TTL
ASSUME /\ Cardinality(Accounts) = 2
       /\ Cardinality(Branches) = 2
       /\ MaxValue >= 1 /\ TTL >= 3

Open == {"offered", "accepted", "committed"}
States == Open \cup {"none", "final", "cancelled", "expired"}

VARIABLES balance, locked, state, sender, receiver, value, round,
          offeredRound, commitRound, initialSupply
vars == <<balance, locked, state, sender, receiver, value, round,
          offeredRound, commitRound, initialSupply>>

Init == /\ balance \in [Accounts -> 0..MaxValue]
        /\ locked = [a \in Accounts |-> 0]
        /\ state = [b \in Branches |-> "none"]
        /\ sender = [b \in Branches |-> CHOOSE a \in Accounts: TRUE]
        /\ receiver = [b \in Branches |-> CHOOSE a \in Accounts: TRUE]
        /\ value = [b \in Branches |-> 0]
        /\ offeredRound = [b \in Branches |-> 0]
        /\ commitRound = [b \in Branches |-> 0]
        /\ round = 0
        /\ initialSupply = balance

Offer(b, s, r, v) ==
  /\ state[b] = "none" /\ s # r /\ v \in 1..MaxValue
  /\ balance[s] - locked[s] >= 2 * v
  /\ state' = [state EXCEPT ![b] = "offered"]
  /\ sender' = [sender EXCEPT ![b] = s]
  /\ receiver' = [receiver EXCEPT ![b] = r]
  /\ value' = [value EXCEPT ![b] = v]
  /\ offeredRound' = [offeredRound EXCEPT ![b] = round]
  /\ locked' = [locked EXCEPT ![s] = @ + 2 * v]
  /\ UNCHANGED <<balance, round, commitRound, initialSupply>>

Accept(b) ==
  /\ state[b] = "offered" /\ round < offeredRound[b] + TTL
  /\ state' = [state EXCEPT ![b] = "accepted"]
  /\ UNCHANGED <<balance, locked, sender, receiver, value, round,
                  offeredRound, commitRound, initialSupply>>

Commit(b) ==
  /\ state[b] = "accepted" /\ round < offeredRound[b] + TTL
  /\ state' = [state EXCEPT ![b] = "committed"]
  /\ commitRound' = [commitRound EXCEPT ![b] = round]
  /\ UNCHANGED <<balance, locked, sender, receiver, value, round,
                  offeredRound, initialSupply>>

Cancel(b) ==
  /\ state[b] \in {"offered", "accepted"}
  /\ state' = [state EXCEPT ![b] = "cancelled"]
  /\ locked' = [locked EXCEPT ![sender[b]] = @ - 2 * value[b]]
  /\ UNCHANGED <<balance, sender, receiver, value, round,
                  offeredRound, commitRound, initialSupply>>

Expire(b) ==
  /\ state[b] \in {"offered", "accepted"}
  /\ round >= offeredRound[b] + TTL
  /\ state' = [state EXCEPT ![b] = "expired"]
  /\ locked' = [locked EXCEPT ![sender[b]] = @ - 2 * value[b]]
  /\ UNCHANGED <<balance, sender, receiver, value, round,
                  offeredRound, commitRound, initialSupply>>

Finalize(b) ==
  /\ state[b] = "committed" /\ round >= commitRound[b] + 3
  /\ state' = [state EXCEPT ![b] = "final"]
  /\ balance' = [balance EXCEPT ![sender[b]] = @ - value[b],
                                ![receiver[b]] = @ + value[b]]
  /\ locked' = [locked EXCEPT ![sender[b]] = @ - 2 * value[b]]
  /\ UNCHANGED <<sender, receiver, value, round,
                  offeredRound, commitRound, initialSupply>>

Tick == /\ round' = round + 1
        /\ UNCHANGED <<balance, locked, state, sender, receiver, value,
                        offeredRound, commitRound, initialSupply>>

Next == (\E b \in Branches, s, r \in Accounts, v \in 1..MaxValue:
           Offer(b, s, r, v))
        \/ (\E b \in Branches: Accept(b) \/ Commit(b) \/ Cancel(b)
                             \/ Expire(b) \/ Finalize(b))
        \/ Tick

LockedFor(a) == Cardinality({p \in Branches \X (1..(2 * MaxValue)):
                              state[p[1]] \in Open /\ sender[p[1]] = a
                              /\ p[2] <= 2 * value[p[1]]})
LockedMatchesBranches == \A a \in Accounts: locked[a] = LockedFor(a)
SupplyConservation ==
  Cardinality({p \in Accounts \X (1..(2 * MaxValue)): p[2] <= balance[p[1]]}) =
  Cardinality({p \in Accounts \X (1..MaxValue): p[2] <= initialSupply[p[1]]})
NonNegative == \A a \in Accounts: 0 <= locked[a] /\ locked[a] <= balance[a]
TypeOK == /\ balance \in [Accounts -> 0..(2 * MaxValue)]
          /\ state \in [Branches -> States]
          /\ round \in Nat

StateConstraint == round <= 5
Spec == Init /\ [][Next]_vars
=============================================================================
