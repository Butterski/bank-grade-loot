---
name: planning-dupe-fixes
description: Turns a dupe review (the findings of finding-dupes, an incident report, or any list of duplication bugs in trade, shop, wallet, inventory, reward, refund or save code) into a fix plan - the order of the fixes by what each bug creates and how easily a player can trigger it, the smallest change per finding with its schema migration and clean-up of the data already damaged (double owners, negative balances, paid purchases without grants, split saves), the test that proves each fix, what to ship first and what can wait, and what to tell support. Use when asked to plan, prioritise, scope or estimate the fixes after a dupe or exploit, to write a remediation or incident plan for item or currency duplication, or to decide whether to switch trading off while fixing. Plans the code; does not write it. From the talk "Bank-Grade Loot" (GIC 2026).
---

# Planning dupe fixes

A review says what breaks. The plan says in which order to fix it, what each fix drags along (a migration, a
clean-up, a client change), how to know it worked, and what to do while the fix is not yet out. The order matters
more than it looks: a unique key added before the duplicates are cleaned up fails to create; an idempotency check
without an operations table has nowhere to look; trading switched off in a hurry has its own bugs.

Copy this checklist and tick it as you go:

```
Fix plan:
- [ ] 1. The findings, one line each
- [ ] 2. Severity and exposure
- [ ] 3. Stop the bleeding, or not
- [ ] 4. One fix per finding, from the catalogue
- [ ] 5. Order and dependencies
- [ ] 6. Clean-up of what is already out there
- [ ] 7. Proof: one test per fix
- [ ] 8. The plan, in the template
```

## 1. The findings

Start from a review. If there is none, say so and run `finding-dupes` first (or ask for its report); a plan built
on a hunch fixes the wrong thing first. List each finding as `path, check, what it creates`, in the review's words.

## 2. Severity and exposure

Rank by two questions:

- **What does it create?** A duplicated item or currency outranks a lost one, which outranks a wrong answer, which
  outranks "cannot tell". Duplication compounds: the first dupe is an advertisement, and more players start looking.
- **Who can trigger it, and how?** A player who can choose the moment (a trade, then a forced disconnect; two
  clients pressing Buy together) has a button. A race that needs two strangers at the same millisecond is a lottery.
  Buttons first.

Then the blast radius: is the value tradeable or sellable for real money, is it on a path with a store charge, how
many players have the feature.

## 3. Stop the bleeding, or not

Switching a feature off is itself a code path, written in a hurry. New World disabled trading in 2021 and the patch
notes then fixed gold duplicating on a path "we introduced by disabling trading". If the plan switches something
off: test the "no" path (what does a blocked action do with the refund, the reservation, the queue?), say what
players lose, and date the switch-on. Prefer a narrow switch (one item class, one operation) to a broad one.

## 4. One fix per finding

Read [references/fix-catalog.md](references/fix-catalog.md) now. It has, per check, the smallest fix that makes it
hold, the migration it needs, the clean-up of existing data, the test that proves it, and the common mistakes when
applying it. Pick the entry for the finding's check; adapt the names, not the shape. Where two findings share a fix
(the operations table serves checks 02 and 04), say so once.

## 5. Order and dependencies

A dependable order, to be adapted:

1. **Measure** before touching anything: the clean-up queries from the catalogue, run read-only, tell how much is
   already out there.
2. **Narrow switch-off**, if exposure is a button and the fix is days away.
3. **One owner row and the transaction** (checks 01 and 03): the schema and the handler, behind a feature flag per
   operation.
4. **The operations table claimed first** (checks 02 and 04): needs the client to send an ID on every request and
   to retry with the same ID; ship the server side first (it tolerates clients without an ID by generating one, with
   a log line), the client side next.
5. **Unique grant per purchase** (check 05): clean up double grants before the constraint, or the constraint fails
   to create.
6. **Outbox, publisher, reconciler** (check 06): the reconciler can run read-only first and report, then deliver.
7. **The state split** for in-memory worlds: last and largest; it changes what the loop waits for.
8. **Recovery paths** (refund, restore): the same rules, often the cheapest fixes.

Say for each step what it depends on and what can go out in parallel.

## 6. Clean-up of what is already out there

The fix stops new damage; the plan also says what happens to the old. From the catalogue: queries that find double
owners, negative balances, purchases without grants, grants without purchases, ledgers that do not match balances.
Then the policy, written down before the first correction: correcting entries with a reason, never edits of history;
remove copies from the account that created them before the accounts that only received them; a rollback of the
world only when nothing else can work, because it takes time from players who did nothing wrong. Support needs the
list of affected accounts and the sentence to send.

## 7. Proof

One test per fix, named after the check, that forces the bad ordering: pause one purchase after its read and run
the other; send the same request twice at the same moment; stop the worker before `COMMIT`; drop the reply and
retry with the same ID; deliver the same event twice, then under a new event ID; stop between the purchase and the
send. A fix without its test is a hope. If the project uses the companion model, the design can be run through
every ordering before it is built (`python -m loot`).

## 8. The plan

Use this template:

```markdown
# Fix plan: <repository or module>

## Findings, ranked
| # | Path | Check | Creates | Trigger | Rank |
|---|---|---|---|---|---|
| 1 | trade() memory split | world in memory | duplicated item | a player, by disconnecting | button |

## Now (hours): stop the bleeding
- <narrow switch-off, or "none needed", with the reason and the date to switch back on>
- Measure: <the read-only queries to run first>

## Fixes, in order
### 1. <finding>: <fix in one line>
- Change: <schema, handler, client, in that order>
- Migration: <new table or constraint; backfill; what is reversible>
- Clean-up: <query that finds the damage; the correcting entries>
- Depends on: <step numbers>
- Proof: <the test, named after the check>
- Effort: <S / M / L, in team days> and what makes it L

## Later
- <the state split, the reconciler as delivery, the client ID rollout ...>

## For support and players
- <who is affected, what they will see, the sentence to send>

## Open questions
- <what the review could not tell, and who can answer it>
```

Effort as S / M / L with a reason, not in hours: hours in a plan become promises. Keep the plan to what the
findings support; a fix for a check that holds is scope creep, unless it is the operations table, which every trade
needs sooner or later.

Three things that go wrong in plans written from this template:

- **The ranking contradicts itself.** Sort the table by the "Creates" column first (duplicated, then lost, then wrong
  answer, then cannot tell) and only then by trigger; a loss ranked above a duplication needs a stated reason.
- **A step without proof.** Every numbered step gets a "Proof" line, including the ones that are not code: a
  switch-off is proved by exercising the disabled path, a watch or an alert by a drill that fires it, a clean-up by
  the query returning zero rows afterwards.
- **Handler code in the plan.** The plan names the fix and the catalogue entry; the SQL it carries is migrations and
  clean-up queries. The handler is written when the step is picked up, by whoever picks it up, with the tests.
