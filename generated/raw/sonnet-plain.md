```python
def buy(db, op_id, buyer, seller, item, price):
    # NOTE: the schema has no listing/offer table, so `price` is trusted as given.

    # Input that can never be valid is refused before touching the database.
    # (A negative price would let the "buyer" take coins from the seller.)
    if isinstance(price, bool) or not isinstance(price, int) or price <= 0:
        return Reply(False, "price must be a positive whole number of coins")
    if buyer == seller:
        return Reply(False, "you cannot buy your own item")

    # One transaction: the item changes owner, the buyer pays and the seller is
    # paid, or nothing happens at all.
    tx = db.begin()

    # Locking order is the same for every purchase: the item first, then the two
    # wallets sorted by player name. Overlapping purchases therefore queue up
    # instead of waiting for each other in a circle (A buys from B while B buys
    # from A). Rows are read with select_for_update only: a plain select never
    # waits and may return a row that is about to change.
    row = yield tx.select_for_update("items", item)
    if row is None:
        yield tx.rollback()
        return Reply(False, "no such item")
    if row["owner"] == buyer:
        # Retry (same op_id) of a purchase that already went through: the item
        # and the coins moved together in one commit, so there is nothing left
        # to do. Do not charge twice and do not report a failure.
        yield tx.rollback()
        return Reply(True, "item %s is already yours, nothing was charged again" % (item,))
    if row["owner"] != seller:
        yield tx.rollback()
        return Reply(False, "%s does not own item %s" % (seller, item))

    wallets = {}
    for player in sorted((buyer, seller)):
        wallets[player] = yield tx.select_for_update("wallets", player)
    if wallets[buyer] is None:
        yield tx.rollback()
        return Reply(False, "buyer has no wallet")
    if wallets[seller] is None:
        yield tx.rollback()
        return Reply(False, "seller has no wallet")
    if wallets[buyer]["coins"] < price:
        yield tx.rollback()
        return Reply(False, "not enough coins")

    # All three rows are locked, so these cannot fail; the checks are only a
    # safety net against ever committing half a trade.
    moved = yield tx.update("items", item, to={"owner": buyer}, add={"version": 1},
                            where={"owner": seller})
    paid = yield tx.update("wallets", buyer, add={"coins": -price},
                           at_least={"coins": price})
    credited = yield tx.update("wallets", seller, add={"coins": price})
    if moved != 1 or paid != 1 or credited != 1:
        yield tx.rollback()
        return Reply(False, "purchase could not be completed, nothing was changed")

    yield tx.commit()
    return Reply(True, "bought item %s from %s for %s coins" % (item, seller, price))
```

MODEL: claude-sonnet-5-5
