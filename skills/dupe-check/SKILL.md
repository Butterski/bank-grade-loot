---
name: dupe-check
description: Review game backend code that creates, moves, consumes or restores value (items, currency, trades, shops, wallets, inventories, loot, rewards, daily claims, refunds, restores, saves) for duplication bugs, using the six checks and three rules from the talk "Bank-Grade Loot" (GIC 2026). Use when asked to check for dupes, item or currency duplication, double spend, double grant, race conditions in trades, idempotency of purchases, or whenever writing or changing code on such a path.
---

# Dupe check

A dupe is a bug that lets a player copy an item or a currency. Most are not exotic: an ordinary operation, in an
unlucky order, and a player who can choose the order. This skill reviews code against the six failures behind most
of them, the way a reviewer ticks a list. Each check comes from the companion repository of the talk, where it is
run against every ordering of the statements and a stop at every point (`python -m loot`).

Scope: every path where value is created, transferred, consumed or restored. Trading, shops, wallets, inventories,
loot drops, rewards, daily claims, refunds, item restores, the save system. Not in scope: client cheats, movement,
anything that never touches a balance or an inventory.

## How to run the review

1. **List the value paths.** Search for the words of the domain: trade, buy, purchase, sell, grant, claim, reward,
   refund, restore, rollback, inventory, wallet, balance, coins, gems, item, transfer, save, persist. For each path
   write one line: the operation, what it reads, what it writes, and where the truth lives (one database transaction,
   several services, or memory that is saved later).
2. **Read how the storage behaves before judging anything.** The isolation level (PostgreSQL defaults to READ
   COMMITTED, where a plain SELECT reserves nothing), whether the ORM or framework opens a transaction per request,
   whether autocommit is on, how a queue retries, when the world is written to disk. Quote the config or the docs.
3. **Ask the six questions below of every path.** For each, find the lock, the constraint or the transaction boundary
   that makes it hold, with file and line. If you cannot find one, the answer is "breaks" or "cannot tell", never
   "probably fine".
4. **Report**, one row per path and check: holds / breaks / cannot tell, the evidence (file:line), for a break the
   shortest ordering that produces it (two or three steps are enough), and the smallest fix. Then the two questions
   from "Beyond the six". Sort the breaks by what they create: a duplicated item or currency first, a lost one next.
5. **Fix only when asked**, one check at a time, and name the check in the commit message. After a fix, say which
   test from "How to check it" proves it.

## The six checks

Each check: what must be true afterwards, what the failing code looks like, and what holds.

### 01 Two buyers compete

One owner, one payment, the other buyer keeps his coins.

- Breaks: read the owner, then write, with nothing between the two that stops a second request; an inventory list
  per player instead of one owner field; a conditional write whose row count nobody looks at. In the model, the
  naive trade duplicates the sword in 910 of 914 orderings. Wrapping the same statements in BEGIN and COMMIT does
  not help: still 238 of 240.
- Holds: one row per item with one owner column. Either lock the rows you will change (SELECT ... FOR UPDATE, the
  wallets in a fixed order, so two trades cannot deadlock), check while holding the locks, then write. Or write
  conditionally (UPDATE ... WHERE owner = seller AND version = v) and treat zero rows changed as "somebody was faster".
- How to check it: pause one purchase right after it reads the owner, run the other to the end, resume the first.

### 02 The same request arrives twice

The stored result, no second transfer.

- Breaks: no operation ID at all; an ID that is looked up at the start of the transaction and stored at the end, so
  two copies in flight both find nothing (20 of 22 orderings give one copy the wrong answer); a repeat with different
  data that is served as a retry.
- Holds: claim the ID first, in the same transaction as the work: INSERT INTO operations (caller, op_id) ... ON
  CONFLICT DO NOTHING under a unique key. Zero rows means the operation exists: read its stored result and return
  it, after comparing the request body. Store the result in the operations row last, before COMMIT.
- How to check it: send the same request twice at the same moment, and once more after the first has answered.

### 03 The process stops before commit

No partial trade.

- Breaks: several statements with no transaction around them (autocommit); "save the purchase, then call the other
  service"; a write to one store followed by a write to another.
- Holds: everything that changes together is written in one transaction: owner, both balances, the ledger entries,
  the result of the operation. A stop before COMMIT leaves nothing; a stop after it leaves everything.
- How to check it: stop the worker before COMMIT, restart, look at the data.

### 04 The reply is lost and the client asks again

One trade, and the right answer the second time.

- Breaks: a timeout handled as "the trade failed", so the client buys again with a new ID; a retry without an ID;
  a server-side retry loop that re-runs the whole handler.
- Holds: the client asks again with the same ID, and check 02 answers from the stored result. A timeout means the
  answer is missing, not that the work failed.
- How to check it: drop the reply, retry with the same ID, count the transfers.

### 05 A paid event arrives twice

One grant per purchase.

- Breaks: a wallet that adds the gems for every message; a unique key on the event ID only, so the same purchase
  announced under a new event ID (a reconciler, a second sender) is granted again; a check that reads first and
  inserts later without a constraint.
- Holds: the grant is recorded with the purchase ID of the provider under UNIQUE (provider, purchase_id), in the
  same transaction as the balance change. A repeat returns the old result. Google Play says the same: check that a
  purchase token has not been used before you grant; Apple retries notifications it thinks you missed.
- How to check it: deliver the same event twice, then the same purchase under a new event ID.

### 06 A payment has no grant

It is found and delivered once.

- Breaks: the purchase is saved, then the wallet is called, and the process dies between the two; or the wallet is
  called first and the purchase is never saved; no job compares what was paid with what was granted.
- Holds: the purchase and an outbox event commit together; a publisher sends the event and resends after a restart;
  a reconciler compares paid purchases with grants on a schedule and delivers what is missing with the same purchase
  ID, never with a new charge. With check 05 in place, the repeats this creates are harmless.
- How to check it: stop the server after the purchase is saved, and before, and after the send; then compare
  purchases with grants.

## Beyond the six

**Where does the world live?** Many game servers keep the world in memory and write each character on its own:
every few minutes, or at logout. A trade changes two characters at once. If the server stops between their two
saves, the disk holds one character from before the trade and one from after it. In the model, 16 histories of a
trade, two saves and one stop: 2 end with two swords, 2 with none. Nobody raced anybody, and a player who can force
the stop has a button. What holds: split the state. Items, coins and purchases go through the database first, in one
transaction, and the player is told only after the commit; the loop never waits for it. Position, health and buffs may
be saved later. Anything that gates a reward (a cooldown, a daily claim flag, a loot lockout) commits with the reward,
or a crash restores the gate while the reward stays.

**Recovery is part of the economy.** A refund must point to a real payment and happen once; a rejected action must
not create a reward. A restore reclaims what the item turned into. A rollback moves value as much as a trade does.
Review these paths with the same six questions.

## The three rules

1. Protect the complete action: all of it, in one transaction, or none of it.
2. Make repeats safe: the same request, the same event, the same answer, once.
3. Account for recovery: crashes, retries, refunds and restores move value too.

## When you write such code yourself

Before writing, state what happens on a live server and make each sentence true in the code. For a purchase:

1. Two players press Buy on the same item at the same moment. One of them gets it. The other one keeps his coins.
2. The same request arrives twice, with the same buyer and the same `op_id`, possibly at the same moment. The purchase happens once, and both requests get the same answer.
3. The server process stops between any two database calls. No half-finished purchase remains.
4. One player sends two different purchases at the same moment. He does not spend coins that he does not have.

For another operation, say the same four things about it: two requests for the same thing, the same request twice,
a stop halfway, one account in two operations. Then run the checks on what you wrote. A prompt raises the odds;
only a check tells you what you got. If the project has a test suite, add one test per check from the "How to check
it" lines. To try a design before building it, write it as a handler for the model in this repository and run
`python -m loot` (see `generated/README.md`).
