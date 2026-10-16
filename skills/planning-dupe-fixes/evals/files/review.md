# Dupe review: `game_backend.py` (shop, gems, world server)

## Storage facts
- Isolation level: READ COMMITTED (PostgreSQL default; nothing in the code sets another)
- Transactions: autocommit (`conn.autocommit = True`, game_backend.py:20); every statement is its own transaction
- Retries: `@retry(3)` re-runs the whole handler on any exception (game_backend.py:26-39); clients retry failed HTTP calls up to three times with a new request each time; the wallet queue is at-least-once
- Saves: `WorldServer` keeps live characters in memory and writes each one to its own JSON file every 120 s and at logout (game_backend.py:118-150)

## Value paths
| # | Path | 01 two buyers | 02 same request | 03 stop before commit | 04 reply lost | 05 event twice | 06 no grant |
|---|---|---|---|---|---|---|---|
| 1 | buy_item (game_backend.py:44-61) | breaks | breaks | breaks | breaks | n/a | n/a |
| 2 | complete_purchase (66-77) | n/a | breaks | breaks | breaks | n/a | breaks |
| 3 | grant_gems (80-91) | n/a | n/a | breaks | n/a | breaks | n/a |
| 4 | refund via request_trade (104-111) | n/a | breaks | holds | breaks | n/a | n/a |
| 5 | WorldServer.trade (126-135) | holds (single thread) | n/a | breaks (saves) | n/a | n/a | n/a |
| 6 | WorldServer.claim_daily (137-143) | n/a | n/a | breaks (saves) | n/a | n/a | n/a |

## Beyond the six
- Where the world lives: breaks. The in-world trade changes both characters in memory; each is saved on its own. A stop between the two saves leaves the Crimson Blade in two files (the seller's old file and the buyer's new one) or in none. A player can force the stop by logging out right after the trade (that saves only his side) and waiting for the next crash or deploy.
- Recovery paths: breaks. `refund` credits coins with no payment row; the trading-disabled branch of `request_trade` calls it for a purchase that never charged anything.

## Breaks, worst first
### 1. WorldServer.trade: one item in two save files
- Evidence: game_backend.py:129-133 moves the item and the coins in memory; `_save` (148-150) writes one player; `on_logout` (144-146) saves only the player who left.
- Shortest ordering: 1. A trades the blade to B in memory. 2. B logs out; B.json has the blade. 3. The server stops before A's autosave. 4. Restart: A.json (old) has the blade, B.json has it too.
- Creates: a duplicated item (and the coins B paid are gone from the world).
- Trigger: a player, by logging out after the trade; the stop comes with the next deploy.

### 2. buy_item: two buyers, one item
- Evidence: 47-49 reads `owner_id` with a plain SELECT; 55 writes it; nothing between them locks or conditions the write.
- Shortest ordering: 1. Stan's request reads owner = Cartman. 2. Kenny's request reads owner = Cartman. 3. Stan's request writes owner = Stan and charges Stan. 4. Kenny's writes owner = Kenny and charges Kenny.
- Creates: a paid buyer without the item; with two sellers, one item with two owners in a trade chain.
- Trigger: two clients pressing Buy together; scripted, it is a button.

### 3. buy_item: balances written back as numbers
- Evidence: 51-52 reads `coins`, 56 writes `coins - price` as a value; 57-59 the same for the seller.
- Shortest ordering: 1. Request A reads Stan's coins = 100. 2. Request B (Stan buys something else) reads 100. 3. A writes 0. 4. B writes 0: one of the two payments was free.
- Creates: coins created or destroyed.

### 4. grant_gems and resend_unacknowledged: one purchase granted twice
- Evidence: `grants.event_id` is unique (schema comment, line 7); 82-84 checks for the event, 85-90 inserts and adds gems; `resend_unacknowledged` (96-99) re-sends the same purchase under a new event ID.
- Shortest ordering: 1. The queue delivers E-apple-P42, gems granted. 2. The wallet's reply is lost. 3. Support runs the resend: E-apple-P42-resend-..., a new event ID. 4. The check finds nothing, gems granted again.
- Creates: duplicated currency (paid once, granted twice). Also a race: two deliveries of the same event, both checks find nothing (82-84), both insert; the second insert fails on the unique key but the first UPDATE of gems has already committed under autocommit.
- Trigger: the support tool, or any replay.

### 5. complete_purchase: paid, no gems
- Evidence: 68-72 commits the purchase; 73-76 posts to the wallet; a stop between the two leaves a purchase with no grant; nothing retries or compares.
- Shortest ordering: 1. The purchase row commits. 2. The process dies before the POST. 3. Nobody ever delivers.
- Creates: a lost purchase (and a support ticket, and a refund that creates coins, see 7).

### 6. buy_item: no operation ID; the retry runs the trade again
- Evidence: no ID anywhere in the handler; `@retry(3)` (26-39, 44) re-runs the whole function after any exception, including one raised after the writes.
- Shortest ordering: 1. The trade's updates commit (autocommit). 2. The seller's balance read raises (connection blip). 3. The decorator runs the handler again: the owner is now the buyer, "item unavailable". Or, with a blip before the last UPDATE, the buyer is charged twice by two clients retrying.
- Creates: a wrong answer about a trade that happened; double charges on client retries.

### 7. request_trade: a refund for a payment that was never taken
- Evidence: 108-110 calls `refund` when trading is disabled; the client only displayed the coins as spent; nothing was charged.
- Shortest ordering: 1. Ops switch trading off. 2. A player presses Buy. 3. +price coins, from nowhere, per press.
- Creates: coins created. Trigger: a button, exactly while the team is busy with another incident.

### 8. claim_daily: the cooldown is in the state saved later
- Evidence: 139-141 sets the cooldown and adds 50 coins in memory; both are saved together, but a stop before the save restores yesterday's cooldown with today's coins only if the coins were persisted elsewhere; here both are lost together (no dupe), unless the reward is later moved to the database and the cooldown is not.

## Cannot tell
- Whether the wallet service and the game share one database in production (the docstring says "same database for now"); if they do, grants and gems could be one transaction.
- Whether the client sends any request identifier that could serve as an operation ID.
