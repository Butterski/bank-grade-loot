I run a small game server and I am adding trading between players.

Write the function that handles a purchase:

    def buy(db, op_id, buyer, seller, item, price):

`buyer` buys the item with the id `item` from `seller` for `price` coins. `op_id` is an ID that the game client attaches to each request.

Return `Reply(True, "...")` when the purchase went through and `Reply(False, "...")` when it did not. `Reply` and `UniqueViolation` are already defined; do not import anything.

These things happen on a live server, and none of them may break anything:

1. Two players press Buy on the same item at the same moment. One of them gets it. The other one keeps his coins.
2. The same request arrives twice, with the same buyer and the same `op_id`, possibly at the same moment. The purchase happens once, and both requests get the same answer.
3. The server process stops between any two database calls. No half-finished purchase remains.
4. One player sends two different purchases at the same moment. He does not spend coins that he does not have.

## The database

Tables:

- `items`: one row per item. Key: the item id (column `id`). Columns: `owner` (a player name), `version` (a number).
- `wallets`: one row per player. Key: the player name (column `player`). Column: `coins` (a number).
- `operations`: no rows yet. Key: the tuple `(caller, op_id)`. Store any columns you like.

Every call below returns a statement. Send it to the database with `yield`, and the result comes back:

    wallet = yield db.select("wallets", "Stan")      # {"player": "Stan", "coins": 100}

So `buy` is a generator, and every database call in it is written as `result = yield ...`.

The calls. Rows come back as dicts, a missing row as `None`:

    select(table, key)
    select_for_update(table, key)       # only inside a transaction: the row stays locked until the transaction ends
    select_where(table, where={...})    # a list of the rows whose columns equal the given values
    update(table, key, to={...}, add={...}, where={...}, at_least={...})
        # `to` sets columns, `add` adds to numbers
        # `where` and `at_least` are conditions: equal to, greater than or equal to
        # returns the number of rows changed: 1, or 0 when the row is missing or a condition fails
    insert(table, key, row, on_conflict_do_nothing=False)
        # returns 1. If the key exists: raises UniqueViolation, or returns 0 with on_conflict_do_nothing=True
    delete_where(table, where={...})    # returns the number of rows deleted

You can call them on `db`. Then each statement is committed on its own:

    changed = yield db.update("wallets", "Stan", add={"coins": -100})

Or you can call them inside a transaction:

    tx = db.begin()                     # no yield on this line
    row = yield tx.select("items", 17)
    ...
    yield tx.commit()                   # or: yield tx.rollback()

Inside a transaction, other requests do not see your changes until you commit. A row that you updated, deleted or selected for update stays locked until your transaction ends. Another `update`, `delete_where` or `select_for_update` of that row waits, and then sees the row as it is after you finished. An insert of a key that another open transaction has just inserted waits in the same way. A plain `select` or `select_where` never waits: it returns the last committed rows.

Reply with one Python code block that contains the function `buy`, and nothing outside the code block.
