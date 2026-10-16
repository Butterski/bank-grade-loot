---
name: designing-trades
description: Designs and writes game backend code that moves value without creating or losing any - player-to-player trades, shop purchases, wallets and currency, inventories, loot and rewards, daily claims, refunds, item restores, and the save system around them. Covers the schema (one owner row, an operations table, a ledger), the transaction (claim the operation ID first, lock in a fixed order, check, write, commit, then confirm), the outbox and reconciler for paid purchases, and what may stay in memory. Use whenever adding or changing trading, shops, wallets, loot, rewards or saves, when deciding what the game loop waits for or which service owns what, or when asked to make a purchase or trade safe, atomic, idempotent or dupe-proof. From the talk "Bank-Grade Loot" (GIC 2026) and its companion model, which checks every ordering of the statements.
---

# Designing trades

A dupe is an ordinary operation in an unlucky order, with a player who can choose the order. So the design goal is
not "handle the race" but "no ordering of these statements creates or destroys value". Write that down before the
code, as four sentences, and make each one true in the code.

Copy this checklist and tick it as you go:

```
Trade design:
- [ ] 1. The four sentences for this operation
- [ ] 2. Where value lives (rows, services, memory)
- [ ] 3. The shape: one transaction, outbox, or state split
- [ ] 4. The handler, in the reference order
- [ ] 5. Unique keys for every repeat
- [ ] 6. One test per sentence
```

## 1. The four sentences

Write them for the operation at hand, in the words of the game. For a purchase they are:

1. Two players press Buy on the same item at the same moment. One of them gets it. The other one keeps his coins.
2. The same request arrives twice, with the same buyer and the same `op_id`, possibly at the same moment. The purchase happens once, and both requests get the same answer.
3. The server process stops between any two database calls. No half-finished purchase remains.
4. One player sends two different purchases at the same moment. He does not spend coins that he does not have.

For any other operation say the same four things: two requests for the same thing, the same request twice, a stop
halfway, one account in two operations at once. They are the acceptance tests; put them in the ticket and the PR.

**Example, a daily reward:**
1. Two claims of the same reward at the same moment: one grant, the second claim is told it was already claimed.
2. The same claim request twice: one grant, both get the same answer.
3. The server stops between marking the claim and granting the reward: either both happened or neither.
4. The player claims today's reward and buys from the shop at the same moment: the two cannot touch the same
   balance without one waiting for the other.

## 2. Where value lives

Decide, and write it into the design:

- **One row per item, one owner column.** Not an inventory list per player: a list on each side is how one item ends
  up in two inventories. Stackable goods are a count on one row per (owner, kind).
- **One balance row per player per currency**, changed only by `UPDATE ... SET coins = coins - 100` style statements,
  never by reading and writing back a number.
- **A ledger** (one row per side of every transfer, with the operation ID) and an **operations table** (who asked,
  which ID, the stored result). Both are cheap to add now and impossible to reconstruct later.
- **What the game loop keeps in memory**: position, health, buffs, cooldowns that gate nothing. Anything that gates a
  reward (a claim flag, a loot lockout, a crafting timer) lives with the reward and commits with it, or a crash
  restores the gate while the reward stays.

## 3. The shape

Pick by where the writes go. Defaults, with the escape hatch:

- **Everything in one database** (items, wallets, operations): one transaction. The reference design in
  [references/reference-design.md](references/reference-design.md), section 3. This is the default; most games
  with one database need nothing else.
- **Two systems** (a purchase recorded by the game, gems held by a wallet service, a store that charged the card):
  the purchase and an outbox event commit together, a publisher sends the event and resends after a restart, the
  wallet grants once per purchase ID, and a reconciler compares purchases with grants. Section 5.
- **The world lives in memory and is saved per character**: split the state. Items, coins and purchases go through
  the database first; the player sees "pending" until the commit; the loop never waits. Section 6.

## 4. The handler, in the reference order

For the one-transaction shape, the statements go in this order and no other:

1. `BEGIN`
2. Claim the operation: `INSERT INTO operations (caller, op_id, request) ... ON CONFLICT DO NOTHING`. Zero rows
   means this request ran before: read the stored result and return it, after checking the request body matches.
   Claiming first is what makes two copies in flight safe; looking the ID up first and storing it last leaves a
   window in which both copies find nothing.
3. Lock what you will change, in a fixed order: the item, then the wallets sorted by key. `SELECT ... FOR UPDATE`.
   A fixed order is the whole protection against deadlocks between two trades in opposite directions.
4. Check while holding the locks: still the seller's? Can the buyer pay? Offer still open?
5. Write: owner, both balances (`coins = coins - price`), two ledger rows, then the result into the operations row.
6. `COMMIT`.
7. Only now tell the player. A reply before the commit is durable is a reply about something that may never happen.

The optimistic variant (conditional `UPDATE ... WHERE owner = $seller AND version = $v`, zero rows means someone
was faster, stop or retry the whole trade) is fine when contention is rare; it still claims the operation first and
still commits everything together. Section 4 of the reference has both.

## 5. Unique keys for every repeat

Repeats arrive from clients, queues, stores and reconcilers. Each gets a unique key on the thing that must happen
once, and a repeat returns the old result instead of running again:

| Repeat of | Unique key | Returns |
|---|---|---|
| a client request | `operations (caller, op_id)` | the stored result |
| a wallet grant | `grants (provider, purchase_id)` | "already granted" |
| an outbox event | the event row itself; the publisher marks it sent | nothing new |

A unique key on the event ID alone is a trap: the same purchase announced under a new event ID (a reconciler, a
second sender) is granted again. Key on the purchase.

## 6. One test per sentence

Each sentence has one test, and the test forces the bad ordering instead of hoping for it:

1. Pause one purchase right after it reads the owner, run the other to the end, resume the first.
2. Send the same request twice at the same moment, then once more after the first answered.
3. Stop the process before `COMMIT`, restart, look at the data.
4. Two purchases from one wallet at the same moment, each for the whole balance.

If the project has a test suite, these go in it, named after the sentence. If you want to try the design before
building it, write it as a handler for the model in this repository and run `python -m loot`; section 9 of the
reference says how. The model tries every ordering, which no test suite does.

## What not to build

- Read the owner, decide, write: the read reserves nothing under READ COMMITTED.
- An inventory list per player.
- The operation ID looked up at the start and stored at the end.
- A retry loop around the whole handler without an ID, or a client that treats a timeout as "failed" and buys again.
- A unique key on the event ID only.
- "Confirmed" shown before the commit is durable; a worker queue with no limit (it hides the overload until it falls
  over).
- Cooldowns, claim flags and lockouts in the part of the state that is saved later.
