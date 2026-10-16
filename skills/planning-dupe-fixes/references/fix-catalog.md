# Fix catalogue

## Contents

- 01 Two buyers compete
- 02 The same request arrives twice (and 04, the reply is lost)
- 03 The process stops before commit
- 05 A paid event arrives twice
- 06 A payment has no grant
- The world lives in memory
- Recovery paths: refunds, restores, rollbacks
- Clean-up queries
- Correcting entries, not rollbacks

Per check: the smallest fix, its migration, the clean-up of data already damaged, the test that proves it, and
the mistakes that usually come with the fix. Table and column names are the reference design's; keep the shape,
adapt the names.

## 01 Two buyers compete

**Smallest fix:** one row per item with one owner column; the trade locks the item and both wallets with
`SELECT ... FOR UPDATE` in a fixed order (item, then wallets sorted by key), checks after the locks, writes, commits.
Where contention is rare, the conditional write instead: `UPDATE items SET owner = $buyer, version = version + 1
WHERE id = $item AND owner = $seller AND version = $v`, and zero rows means stop or retry the whole trade. Balances
change by `coins = coins - $price`, never by writing back a number that was read.

**Migration:** if items live in per-player lists (an array column, a document, a JSON blob), add the `items` table
with `owner`, backfill from the lists, switch reads to the table behind a flag, switch writes, then drop the lists.
A `CHECK (coins >= 0)` on wallets catches the double spend the moment it is tried.

**Clean-up:** the double-owner query below. Decide which copy stays (the one that was paid for, by the ledger or
by the trade log) before the backfill, or the backfill picks at random.

**Proof:** pause one purchase right after it reads the owner, run the other to the end, resume the first. One
owner, one payment, the other buyer has his coins and a clear "no".

**Mistakes:** locking the item but reading the wallet plainly (the double spend moves to the wallet); `FOR UPDATE`
under autocommit, which releases the lock when the statement returns; locking wallets in request order, which
deadlocks two trades in opposite directions; a conditional write whose row count is not read.

## 02 The same request arrives twice, and 04 the reply is lost

**Smallest fix:** an `operations` table keyed on `(caller, op_id)` with `request` and `result` columns. The handler
claims the row first (`INSERT ... ON CONFLICT DO NOTHING`); zero rows means the request ran before: compare the
request body, read the result, return it. The result is written into the row before `COMMIT`. The client sends an
ID with every request and retries with the same ID; a timeout is "ask again", not "failed".

**Migration:** new table, no backfill. Server side first: a request without an ID gets one generated and a log
line, so old clients keep working and the logs show when they are gone. Client side next. Keep rows at least as
long as a client may retry (days); a scheduled delete after that.

**Clean-up:** none for the table. Repeated trades already executed show up in the ledger as two operations with
the same request from the same caller within seconds; the query below lists them for a correcting entry.

**Proof:** send the same request twice at the same moment, and once more after the first answered: one transfer,
the same answer three times. Then drop the reply, retry with the same ID, count the transfers.

**Mistakes:** looking the ID up at the start and writing it at the end (two copies in flight both find nothing);
keeping the ID in a cache or in memory; a unique key on `op_id` alone, which lets one player block another's ID;
answering the repeat with "already done" as an error when the first answer was a success.

## 03 The process stops before commit

**Smallest fix:** one transaction around everything that changes together: owner, both balances, ledger rows,
the operation's result. Explicit `BEGIN` and `COMMIT` where the framework autocommits; one session per request in
ORMs that flush per call.

**Migration:** none in the schema. In the code, find every place that writes to two stores in a row (the catalogue
entry for 06 covers the case where the second store is another service).

**Clean-up:** half-finished trades are items whose owner changed without a matching ledger pair, or ledger rows
with one side; the queries below. Each one is a correcting entry, decided per case.

**Proof:** stop the worker before `COMMIT`, restart, look at the data: nothing changed. Stop after `COMMIT`: all
of it changed, and the stored result says so.

**Mistakes:** a transaction that ends before the result is stored; a `try/except` that swallows the error after a
partial write and replies "ok"; a savepoint released early.

## 05 A paid event arrives twice

**Smallest fix:** a `grants` table with `UNIQUE (provider, purchase_id)`; the wallet inserts the grant and adds the
gems in one transaction; a conflict returns "already granted" with the original amount.

**Migration:** create the table; backfill one grant row per purchase that was delivered (from the wallet's history
or the purchases table). Run the double-grant query first: the constraint cannot be created while duplicates exist.

**Clean-up:** accounts granted twice for one purchase get a correcting entry for the extra grant, with the purchase
ID in the reason. If the gems were spent, the entry still goes in; the balance may go negative and the policy says
what happens then.

**Proof:** deliver the same event twice, then the same purchase under a new event ID: one grant, three "granted"
answers.

**Mistakes:** the unique key on the event ID (a reconciler or a replay under a new ID grants again); "look for an
earlier grant, then add" with no constraint, which passes every single-threaded test; granting outside the
transaction that records the grant.

## 06 A payment has no grant

**Smallest fix:** the purchase and an outbox event commit together in the game database; a publisher sends unsent
events and marks them after the wallet answers, starting from the top after a restart; a reconciler compares the
store's paid purchases with the wallet's grants on a schedule and sends what is missing with the same purchase ID.
With 05 in place, every repeat this creates is harmless.

**Migration:** `outbox` table; the publisher as a job; the reconciler first in report-only mode (it lists the
missing grants and does nothing), then delivering. Verify purchases with the store on the server before recording
them: a receipt from the client is a claim.

**Clean-up:** the reconciler's first report is the clean-up list: paid purchases with no grant, delivered once each.

**Proof:** stop the server after the purchase is saved, before the send, after the send; restart; every paid
purchase has exactly one grant.

**Mistakes:** calling the wallet first and saving the purchase after; a publisher that marks the event sent before
the wallet answered; a reconciler that creates a new charge or a new purchase instead of delivering the existing
one; acknowledging the store's notification before the grant committed.

## The world lives in memory

**Smallest fix:** the state split. Items, coins, purchases and everything that gates a reward go through the
database first, in the transaction of 01 and 03, run by a worker; the loop marks the item pending (no other command
may sell, trade, drop or consume it) and shows "confirmed" only after the commit; position, health and buffs stay in
the per-character save. The worker queue has a limit.

**Migration:** the biggest one in this catalogue. Introduce the database path for one operation (the trade) behind
a flag while the saves keep running; move the item and coin columns out of the save file into the database with a
one-time import; keep the save path for the rest. Reward gates (cooldowns, claim flags, lockouts) move with the
rewards they gate.

**Clean-up:** items present in two save files, or in a save file and in the database, after the import; the
double-owner query adapted to the save format.

**Proof:** stop the server at every point of a trade and restart: one item in every history. The companion model
counts 16 histories for a trade, two saves and a stop; all 16 end with one item once the trade is in one transaction.

**Mistakes:** "pending" that exists only in the UI; confirming when the worker accepted the job rather than when
the commit returned; an unbounded queue; a cooldown saved later than the reward it gates.

## Recovery paths: refunds, restores, rollbacks

**Refund:** `refunds (payment_id UNIQUE)`, created only from a payment row; a rejected action takes no refund path.
Proof: reject an action and check the balance; refund twice and check once.

**Restore:** restoring an item reclaims what it turned into (the outputs of a disenchant, the materials of a craft),
with a cooldown per character and a lock on the character while it runs. Proof: restore while another command tries
to move the outputs.

**Rollback of the world:** only when correcting entries cannot work. It takes time from every player; say so in
the plan and prefer the entries.

## Clean-up queries

Read-only first. Adapt names.

```sql
-- items with more than one owner (per-player lists or a denormalised owner column)
SELECT item_id, count(*) FROM inventory GROUP BY item_id HAVING count(*) > 1;

-- balances below zero
SELECT player, coins FROM wallets WHERE coins < 0;

-- paid purchases without a grant, and grants without a purchase
SELECT p.provider, p.purchase_id FROM purchases p
  LEFT JOIN grants g USING (provider, purchase_id) WHERE g.purchase_id IS NULL;
SELECT g.provider, g.purchase_id FROM grants g
  LEFT JOIN purchases p USING (provider, purchase_id) WHERE p.purchase_id IS NULL;

-- the same purchase granted twice
SELECT provider, purchase_id, count(*) FROM grants GROUP BY provider, purchase_id HAVING count(*) > 1;

-- ledger rows without their other side, and operations run twice by the same caller within a minute
SELECT op_id FROM ledger GROUP BY op_id HAVING sum(amount) <> 0;
SELECT caller, request, count(*) FROM operations
  WHERE at > now() - interval '30 days' GROUP BY caller, request, date_trunc('minute', at) HAVING count(*) > 1;

-- ledger against balances
SELECT w.player, w.coins, coalesce(sum(l.amount), 0) AS booked FROM wallets w
  LEFT JOIN ledger l ON l.account = w.player GROUP BY w.player, w.coins HAVING w.coins <> coalesce(sum(l.amount), 0);
```

## Correcting entries, not rollbacks

A correction is a new ledger row with the reason and the reference to what it corrects (`op_id`, `purchase_id`,
the incident). History stays readable, support can explain every balance, and the players who did nothing wrong
keep their evening. Two public precedents, if the plan wants to cite them (sources: GameSpot and PC Gamer on
Diablo III, May 2013; Dot Esports on New World, November 2021): Diablo III took the auction houses offline, audited
accounts and recovered most of the duplicated gold without rolling the game back; New World switched wealth
transfers off twice in two weeks and everyone lost trading, not only the cheaters. The plan should say which of
the two it is choosing and why, and cite a precedent only with its source or not at all.
