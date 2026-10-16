---
name: finding-dupes
description: Finds duplication bugs (dupes) in existing game backend code - races between two buyers, repeated requests, crashes between statements, payment events delivered twice, paid purchases never granted, refunds without a payment, and worlds kept in memory and saved one character at a time. Lists every path that creates, moves, consumes or restores value, then answers six checks per path with file and line, the shortest ordering that breaks it, and the smallest fix. Use when asked to review, audit or check trading, shop, wallet, inventory, loot, reward, refund or save code for dupes, double spend, double grant, race conditions or idempotency; when an item or currency duplication was reported; or before shipping a change on any such path. From the talk "Bank-Grade Loot" (GIC 2026).
---

# Finding dupes

Most dupes are an ordinary operation in an unlucky order, with a player who can choose the order. A review that
reads one request from top to bottom sees nothing wrong, so this review asks six fixed questions of every path that
moves value, and demands the lock, the constraint or the transaction boundary that answers each one.

Copy this checklist and tick it as you go:

```
Dupe review:
- [ ] 1. Inventory of value paths (run the scanner, then read)
- [ ] 2. How the storage behaves (quote the config)
- [ ] 3. Six checks per path, with evidence
- [ ] 4. Where the world lives; the recovery paths
- [ ] 5. The report, in the template
```

## 1. Inventory of value paths

Run the scanner on the repository root. It is a grep with a vocabulary, not a judge: it ranks files by how many
words of the domain (wallet, inventory, grant, refund, save ...) and of storage (UPDATE, FOR UPDATE, commit,
autocommit ...) they contain and prints the matching lines, so that no path is forgotten.

```bash
python scripts/find_value_paths.py <repo root>            # top 25 files, 8 lines each
python scripts/find_value_paths.py <repo root> --top 50 --lines 20
```

Then read the files it points at, and the ones you know from the task, and write one line per path:

```
<operation> | reads: <rows/tables> | writes: <rows/tables> | truth lives in: <one transaction / several services / memory saved later>
```

A path is anything that creates, transfers, consumes or restores value: trades, shop purchases, grants, rewards,
daily claims, loot pickups, crafting, refunds, restores, admin tools, migrations, and the save system itself.

## 2. How the storage behaves

Before judging a line of code, find and quote:

- the isolation level (PostgreSQL and MySQL default to READ COMMITTED: a plain `SELECT` reserves nothing);
- whether the framework or ORM opens a transaction per request, and whether autocommit is on;
- what retries: the client, a middleware, a queue, a store's notifications;
- how and when the world is written to disk, if it lives in memory.

The verdicts below depend on these facts. A `SELECT ... FOR UPDATE` under autocommit locks nothing after the
statement returns.

## 3. Six checks per path

Read [references/six-checks.md](references/six-checks.md) now. For every path and every check, find the thing that
makes the check hold and cite it:

| Verdict | Needs |
|---|---|
| holds | a lock, a unique constraint or a transaction boundary at file:line that covers the ordering in question |
| breaks | the shortest ordering that ends with a duplicate, a loss or a wrong answer: two or three numbered steps |
| cannot tell | what is missing to decide: the isolation level, the queue's retry policy, the save interval ... |

"Probably fine" is not a verdict. The burden is on the code: if you cannot point at the line that stops the
ordering, write "cannot tell" and say what you would need.

The shortest ordering reads like this:

```
1. Request A reads items.owner = Cartman            (shop.py:41)
2. Request B reads items.owner = Cartman            (shop.py:41)
3. A writes owner = Stan, charges Stan              (shop.py:48-52)
4. B writes owner = Kenny, charges Kenny            (shop.py:48-52)
End: Kenny owns the sword, Stan paid and has nothing. Both replies said "yes".
```

## 4. Where the world lives; the recovery paths

Two questions the six checks do not cover, from the same reference file, section "Beyond the six": whether a crash
between two per-character saves can leave an item on both sides (or neither), and whether refunds, restores and
rollbacks obey the same rules as the trade. Answer both for the whole system, not per path.

## 5. The report

Use this template. One row per path and check in the table; one block per break below it, worst first (a
duplicated item or currency, then a lost one, then a wrong answer, then "cannot tell").

```markdown
# Dupe review: <repository or module>

## Storage facts
- Isolation level: <value> (<file:line> or docs)
- Transactions: <per request / explicit / autocommit> (<file:line>)
- Retries: <who retries what>
- Saves: <how the world is persisted>

## Value paths
| # | Path | 01 two buyers | 02 same request | 03 stop before commit | 04 reply lost | 05 event twice | 06 no grant |
|---|---|---|---|---|---|---|---|
| 1 | buy_item (shop.py:30) | breaks | breaks | holds | breaks | n/a | n/a |

## Beyond the six
- Where the world lives: <holds / breaks / cannot tell, with the save mechanism and the ordering>
- Recovery paths: <refund, restore, rollback: holds / breaks / cannot tell>

## Breaks, worst first
### 1. buy_item, check 01: two buyers get one sword
- Evidence: <file:line> reads the owner, <file:line> writes it; nothing between them locks or conditions the write.
- Shortest ordering: <numbered steps>
- Creates: a duplicated item / lost coins / a wrong answer
- Smallest fix: <one sentence; the fix catalogue of planning-dupe-fixes has the rest>
- Test that would have caught it: <from "How to check it">

## Cannot tell
- <path, check, what is missing>
```

Keep the findings to what the code shows. Do not patch anything during the review; the plan comes after, with its
own ordering and migrations (the `planning-dupe-fixes` skill, if it is installed). If the user wants a fix at once,
fix one check at a time and name the check in the commit.

## Two habits that keep the review honest

- **Replay every ordering you write down.** Walk the numbered steps against the code once more and check that the
  end state you claim (two copies, none, a wrong answer) is the one the steps produce. An ordering that ends
  somewhere else is a different finding, or none.
- **The counts belong to their check.** The numbers in the reference ("910 of 914") describe the model's naive
  trade under that one check; quote them for the matching check only, never as a figure for the code under review,
  which has its own orderings.

## Things reviewers miss

- An ORM that commits after every call. `session.query(...)` then `session.add(...)` is two transactions.
- `FOR UPDATE` on the item but a plain read of the wallet: the double spend lives in the wallet.
- A unique key on the event ID: the same purchase under a new event ID is granted again.
- The operation ID looked up at the start and written at the end: two copies in flight both find nothing.
- "Pending" that exists only in the UI. Ask what stops a second command from selling the pending item.
- A retry decorator around the whole handler, or a client that treats a timeout as a failure and sends a new ID.
- Cooldowns and claim flags in the state that is saved later than the reward they gate.
- The emergency switch. Trading off changes the paths; the "no" path has to be tested too.
