# Reference design

## Contents

1. What to assume about the database
2. Schema
3. The trade, one transaction
4. The optimistic variant
5. The paid purchase: outbox, publisher, reconciler, wallet
6. The state split for worlds that live in memory
7. Recovery paths
8. What each shortcut costs, counted
9. Trying a design in the model

## 1. What to assume about the database

PostgreSQL at its default isolation level, READ COMMITTED. The same holds for MySQL and most managed databases
unless the code sets a stricter level and retries on serialization errors.

- A plain `SELECT` reserves nothing. Two requests can both read "owner = Cartman" and both proceed.
- `UPDATE`, `DELETE` and `SELECT ... FOR UPDATE` lock the row until `COMMIT` or `ROLLBACK`. A statement that needs
  a locked row waits, then sees the row as it is after the other transaction finished.
- An `INSERT` of a key that another open transaction has just inserted waits the same way, then fails (or does
  nothing with `ON CONFLICT DO NOTHING`).
- When the process stops, its open transaction is rolled back. Statements committed one by one stay.
- Autocommit, which many drivers and ORMs turn on by default, makes every statement its own transaction. Check
  what the framework does before relying on `BEGIN`.

## 2. Schema

```sql
CREATE TABLE items (
  id        bigint PRIMARY KEY,
  owner     text   NOT NULL,          -- one owner, one column
  version   int    NOT NULL DEFAULT 0 -- for the optimistic variant
);

CREATE TABLE wallets (
  player    text   PRIMARY KEY,
  coins     bigint NOT NULL CHECK (coins >= 0)
);

CREATE TABLE ledger (                 -- one row per side of every transfer
  id        bigserial PRIMARY KEY,
  op_id     text   NOT NULL,
  account   text   NOT NULL,
  amount    bigint NOT NULL,
  item_id   bigint,                   -- set on the two rows of an item move
  at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE operations (             -- one row per request the client ever made
  caller    text   NOT NULL,
  op_id     text   NOT NULL,
  request   jsonb  NOT NULL,          -- to refuse a repeat with different data
  result    jsonb,                    -- NULL while in progress
  at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (caller, op_id)
);
```

Keep `operations` rows at least as long as a client may retry (days, not minutes). The ledger is append-only:
corrections are new rows, never edits or deletes.

## 3. The trade, one transaction

Stan (`$buyer`) buys item 17 from Cartman (`$seller`) for 100 coins, request `T42`:

```sql
BEGIN;

INSERT INTO operations (caller, op_id, request)
VALUES ($buyer, 'T42', $request)
ON CONFLICT DO NOTHING;               -- 0 rows: ran before -> read result, compare request, return it

SELECT owner FROM items   WHERE id = 17 FOR UPDATE;
SELECT coins FROM wallets WHERE player IN ($buyer, $seller) ORDER BY player FOR UPDATE;
-- owner = $seller? buyer coins >= 100? offer still open? otherwise ROLLBACK and a clear "no"

UPDATE items   SET owner = $buyer, version = version + 1 WHERE id = 17;
UPDATE wallets SET coins = coins - 100 WHERE player = $buyer;
UPDATE wallets SET coins = coins + 100 WHERE player = $seller;
INSERT INTO ledger (op_id, account, amount, item_id) VALUES
  ('T42', $buyer,  -100, 17),
  ('T42', $seller, +100, 17);
UPDATE operations SET result = '{"ok": true, "item": 17}' WHERE caller = $buyer AND op_id = 'T42';

COMMIT;
-- only now: reply to the client
```

Why each line sits where it does:

- The operation is claimed before anything else, so a second copy of `T42` waits on the primary key, then finds the
  row and returns the stored result. Claimed last, both copies run the trade.
- `ORDER BY player` on the wallet lock is the fixed order. Two trades in opposite directions lock the same two rows
  in the same order and queue instead of deadlocking.
- The checks run while the locks are held. A check before the lock is a check against a value that may change.
- Balances change by arithmetic in the statement, never by writing back a number the code read earlier.
- The result goes into the operations row before `COMMIT`, so a lost reply can be answered from the database.

The reply to the client is the stored result, with the same shape on the first run and on every repeat.

A refusal (not the seller's any more, cannot pay, offer closed) can go two ways, and the design should say which:
`ROLLBACK`, which also drops the claimed operations row, so a repeat runs the checks again and gets the current
answer; or store the refusal as the result and `COMMIT`, so a repeat gets the same "no" even after the state moved
on. The second is simpler to reason about and costs one row; the first is right when the client is expected to
retry a refused request after fixing the cause.

## 4. The optimistic variant

Same claim, same commit, no locks:

```sql
BEGIN;
INSERT INTO operations ... ON CONFLICT DO NOTHING;      -- as above
SELECT owner, version FROM items WHERE id = 17;        -- a plain read

UPDATE items SET owner = $buyer, version = version + 1
 WHERE id = 17 AND owner = $seller AND version = $version_read;
-- 0 rows: somebody was faster -> ROLLBACK, answer "unavailable" (or retry the whole trade once)

UPDATE wallets SET coins = coins - 100 WHERE player = $buyer AND coins >= 100;
-- 0 rows: cannot pay -> ROLLBACK
UPDATE wallets SET coins = coins + 100 WHERE player = $seller;
INSERT INTO ledger ...;
UPDATE operations SET result = ...;
COMMIT;
```

The row counts are the whole check. Ignore them and the design is the naive one with extra steps: in the model,
the conditional write with the row count ignored leaves a buyer paid and without the item in 12 of 14 orderings.

## 5. The paid purchase: outbox, publisher, reconciler, wallet

The store charged the card; that call cannot be inside the game's transaction. Two systems, so the delivery request
has to survive a crash on its own:

```sql
-- game database, one transaction, after the store verified the purchase
BEGIN;
INSERT INTO purchases (provider, purchase_id, account, gems, verified_at) VALUES (...);
INSERT INTO outbox (event_id, purchase_provider, purchase_id, payload) VALUES ('E42', ...);
COMMIT;
```

- **Publisher:** reads unsent outbox rows, calls the wallet, marks the row sent after the wallet answered. After a
  restart it starts from the top, so the wallet may hear the same event twice. That is intended.
- **Wallet:** one transaction per event. `INSERT INTO grants (provider, purchase_id, event_id, gems)` under
  `UNIQUE (provider, purchase_id)`; on conflict return "already granted"; otherwise add the gems in the same
  transaction. Keyed on the purchase, not the event, so a new event ID for the same purchase is harmless.
- **Reconciler:** on a schedule, compares what the store says was paid with what the wallet granted, and sends a
  delivery request for what is missing, with the same purchase ID. Never a new charge. With the wallet keyed on the
  purchase, a reconciler that runs twice is harmless too.
- **The client:** verifies the purchase with the store on the server side first. A receipt from the client is a
  claim, not a payment. Google Play documents checking that a purchase token was not used before granting; Apple
  retries server notifications it believes were missed. Both are repeats by design.

In the model, "save the purchase, then call the wallet" leaves the player paid and without gems in 2 of 4
orderings; the outbox with a wallet that adds gems per event grants 200 for one payment in 8 of 22; the full design
holds in all 13,610.

## 6. The state split for worlds that live in memory

A server that keeps characters in memory and writes each one to disk on its own (every few minutes, or at logout)
loses a trade to a crash between the two saves: the disk then holds one character from before the trade and one
from after it. In the model that is 16 histories of a trade, two saves and one stop: 2 end with two copies of the
item, 2 with none. A player who can force the stop has a button.

Split the state:

- **Through the database first:** items, coins, purchases, and whatever gates a reward (claim flags, loot lockouts,
  timers that unlock a grant). The transaction of section 3, run by a worker so the game loop never waits.
- **Saved later, per character:** position, health, buffs, anything whose loss annoys and enriches nobody.
- **Pending:** from the moment the request leaves the loop until the commit is durable, the item is held: no other
  command may sell, trade, drop or consume it. The player sees "pending"; that is honest.
- **Confirm after commit:** the loop learns of the commit from the worker and only then shows "confirmed".
  A timeout means the answer is missing, not that the trade failed; the client asks again with the same `op_id`.
- **Bounded queue:** the worker queue has a limit and a clear "try again later" when it is full. An unbounded queue
  hides the overload until the process dies with the queue in memory.

With the trade written in one transaction, the same 16 histories end with one item in all 16.

## 7. Recovery paths

They move value as much as a trade does, and they are written in a hurry, so they get the same rules:

- A **refund** points at a real payment row and happens once: `UNIQUE (payment_id)` on refunds. A rejected action
  must not run the refund path; test the "no" path as seriously as the "yes" path.
- A **restore** of an item reclaims what the item turned into (the outputs of a disenchant, the materials of a
  craft), with a cooldown and a lock while it runs. Otherwise "restore" is a polite dupe button.
- A **rollback** of the world is a transfer of everything; prefer correcting entries in the ledger with a reason,
  which keep the history and take nothing from players who did nothing wrong.
- An **emergency stop** (trading off) changes the paths through the code. New World disabled trading in 2021 and
  the patch notes then fixed gold duplicating on a path "we introduced by disabling trading".

## 8. What each shortcut costs, counted

Numbers from `python -m loot`, every ordering of two requests and a stop after every statement:

| Design | Check 01, two buyers | Check 02, same request twice |
|---|---|---|
| naive: read, then write, a list per player | 910 of 914 orderings duplicate the item | 462 of 462 break |
| the same statements in BEGIN ... COMMIT | still 238 of 240 | still 72 of 72 |
| conditional write, row count ignored | 12 of 14 paid without the item | 14 of 14 |
| lock and check, no operations table | holds | 2 of 2 |
| operation looked up first, stored last | holds | 20 of 22 |
| reference: claim first, lock, check, commit | holds | holds |

## 9. Trying a design in the model

The model in this repository runs a handler through every ordering. To try a design:

1. Write the handler as a Python generator in the style of `loot/trade.py`: every database call is
   `row = yield tx.select_for_update("items", item)`, and `buy(db, op_id, buyer, seller, item, price)` returns a
   `Reply`. The calls (`select`, `select_for_update`, `update` with `to`, `add`, `where`, `at_least`, `insert` with
   `on_conflict_do_nothing`, `begin`, `commit`, `rollback`) are described in `generated/prompt-plain.md`.
2. Add it to `TRADE_DESIGNS` in `loot/trade.py` with a name, or drop it into `generated/samples/` and run
   `python -m loot generated`.
3. Run `python -m loot` and read the row for your design. `python -m loot show 02 <name>` prints one broken
   ordering statement by statement, which is the fastest way to see what the code allowed.
