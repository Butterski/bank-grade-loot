# The six checks

## Contents

- 01 Two buyers compete
- 02 The same request arrives twice
- 03 The process stops before commit
- 04 The reply is lost and the client asks again
- 05 A paid event arrives twice
- 06 A payment has no grant
- Beyond the six: where the world lives; recovery paths
- How the numbers were counted

Each check: what must be true afterwards, what the failing code looks like, the shortest ordering that breaks it,
what holds, and how to check it with a test. The numbers come from the companion model of the talk, which runs
each design through every ordering of two requests and a stop after every statement (`python -m loot`).

## 01 Two buyers compete

**Must hold:** one owner, one payment, the other buyer keeps his coins.

**Failing code looks like:** read the owner (or the balance), decide in application code, then write. Nothing
between the read and the write stops a second request. Variants: an inventory list per player (remove from one
list, append to the other); a conditional `UPDATE ... WHERE owner = seller` whose row count nobody reads; a lock
on the item but a plain read of the wallet (the double spend then lives in the wallet).

**Shortest ordering:** A reads owner = seller; B reads owner = seller; A writes and charges; B writes and charges.

**Counted:** the naive trade duplicates the item in 910 of 914 orderings. The same statements between `BEGIN` and
`COMMIT`: still 238 of 240. A conditional write with the row count ignored: a buyer paid without the item in 12 of
14.

**Holds with:** one row per item with one owner column, and either `SELECT ... FOR UPDATE` on the item and both
wallets in a fixed order with the checks after the locks, or a conditional write whose row count is checked (zero
rows means somebody was faster: stop or retry the whole trade). Balances change by `coins = coins - price`, not by
writing back a number that was read.

**How to check it:** pause one purchase right after it reads the owner, run the other to the end, resume the first.

## 02 The same request arrives twice

**Must hold:** the stored result, no second transfer, the same answer to both copies.

**Failing code looks like:** no operation ID; an ID that is looked up at the start of the transaction and written at
the end; a repeat with a different body served as if it were a retry; an ID checked in a cache instead of the
database.

**Shortest ordering:** copy A looks up `T42`, finds nothing; copy B looks up `T42`, finds nothing; both run the trade.

**Counted:** the design that looks the operation up first and stores it last gives one copy the wrong answer in 20
of 22 orderings. Lock-and-check with no operations table: 2 of 2.

**Holds with:** the operation claimed first, in the same transaction as the work: `INSERT INTO operations (caller,
op_id, ...) ON CONFLICT DO NOTHING` under a primary key on `(caller, op_id)`. Zero rows means it exists: compare the
request body, read the stored result, return it. The result is written into the row before `COMMIT`.

**How to check it:** send the same request twice at the same moment, and once more after the first has answered.

## 03 The process stops before commit

**Must hold:** no partial trade; either everything happened or nothing did.

**Failing code looks like:** statements with no transaction around them (autocommit, or an ORM that flushes per
call); a write to one store followed by a write to another; "save the purchase, then call the wallet".

**Shortest ordering:** the owner is changed; the process dies; the buyer was never charged, or the seller never paid.

**Counted:** the naive trade leaves a half-finished trade in 3 of 6 stop points.

**Holds with:** one transaction around everything that changes together: owner, both balances, the ledger rows,
the result of the operation. A stop before `COMMIT` leaves nothing; a stop after it leaves everything.

**How to check it:** stop the worker before `COMMIT`, restart, look at the data.

## 04 The reply is lost and the client asks again

**Must hold:** one trade, and the right answer the second time.

**Failing code looks like:** a client that treats a timeout as "failed" and sends a new request with a new ID; a
retry without an ID; a server-side retry loop that runs the whole handler again; a correct check 02 whose stored
result is written after the commit, so a crash between the two loses the answer.

**Shortest ordering:** the trade commits; the reply is lost; the client asks again; the server finds no result and
trades again, or says "no" about a trade that happened.

**Counted:** lock-and-check with no stored result answers the repeat wrongly in 1 of 8 stop points; the naive
trade breaks in 4 of 6.

**Holds with:** the client asks again with the same ID, and check 02 answers from the stored result. A timeout means
the answer is missing, not that the work failed.

**How to check it:** drop the reply, retry with the same ID, count the transfers.

## 05 A paid event arrives twice

**Must hold:** one grant per purchase.

**Failing code looks like:** a wallet that adds the gems for every message; a unique key on the event ID only, so the
same purchase announced under a new event ID (a reconciler, a second sender, a replayed queue) is granted again; a
"look for an earlier grant, then add" without a constraint, which passes every single-threaded test.

**Shortest ordering:** delivery 1 looks for a grant, finds none; delivery 2 looks, finds none; both add the gems.

**Counted:** look-then-add grants 200 gems for one payment in 56 of 60 orderings; unique on the event ID holds for
the same event and breaks in 8 of 10 for the same purchase under a new ID.

**Holds with:** the grant recorded with the provider's purchase ID under `UNIQUE (provider, purchase_id)`, in the
same transaction as the balance change; a conflict returns "already granted". Google Play documents checking that a
purchase token was not used before granting; Apple retries server notifications it believes were missed.

**How to check it:** deliver the same event twice, then the same purchase under a new event ID.

## 06 A payment has no grant

**Must hold:** a paid purchase is found and delivered once.

**Failing code looks like:** the purchase is saved, then the wallet is called, and the process dies between the
two; or the wallet is called first and the purchase is never recorded; no job compares what the store says was paid
with what the wallet granted.

**Shortest ordering:** the purchase row commits; the process dies before the wallet call; nothing ever retries.

**Counted:** save-then-call leaves the player paid and without gems in 2 of 4 stop points; call-then-save grants
twice or records nothing in 3 of 4; an outbox whose wallet adds gems per event grants 200 for one payment in 8 of
22; the full design holds in all 13,610 orderings.

**Holds with:** the purchase and an outbox event committed together; a publisher that sends and resends after a
restart; a reconciler that compares paid purchases with grants on a schedule and sends what is missing with the
same purchase ID, never a new charge. With check 05 in place, the repeats this creates are harmless.

**How to check it:** stop the server after the purchase is saved, before the send, and after the send; then compare
purchases with grants.

## Beyond the six

**Where the world lives.** A server that keeps characters in memory and saves each one on its own (every few
minutes, or at logout) loses a trade to a stop between the two saves: the disk holds one character from before the
trade and one from after it. Counted: 16 histories of a trade, two saves and one stop; 2 end with two copies of the
item, 2 with none. Nobody raced anybody, and a player who can force the stop has a button. Holds with the state
split: items, coins and purchases through the database first in one transaction, "pending" until the commit, the
player told only after it; position, health and buffs saved later. Anything that gates a reward (claim flags, loot
lockouts, timers) commits with the reward. With the trade in one transaction the same 16 histories all end with
one item.

**Recovery paths.** A refund points at a real payment and happens once (a rejected action must not run the refund
path). A restore reclaims what the item turned into, with a cooldown and a lock while it runs. A rollback moves
value as much as a trade does; correcting entries in the ledger keep the history. An emergency stop changes the
paths through the code: test the "no" path as seriously as the "yes" path.

## How the numbers were counted

Two requests, a handful of statements each, every interleaving, and a process that may stop after any statement.
"Holds" means no ordering in that space breaks the rule. The model follows PostgreSQL's READ COMMITTED: row locks
until commit, waits on locked rows and on inserts of a key another transaction is writing, rollback on a stop. It is
a model, not a benchmark and not a live database; the orderings it finds are real orderings, and the ones it
cannot represent (more than two requests, a replica, a cache) are not covered.
