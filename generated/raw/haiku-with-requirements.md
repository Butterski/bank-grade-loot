```python
def buy(db, op_id, buyer, seller, item, price):
    # The caller of a request is the buyer, so (buyer, op_id) identifies one request.
    tx = db.begin()
    try:
        # Claim the request first. A second copy waits here until the first copy ends.
        # If the first copy committed, the insert returns 0 and we only repeat its answer.
        # If the first copy rolled back, the insert succeeds and this copy does the purchase.
        claimed = yield tx.insert(
            "operations", (buyer, op_id),
            {"item": item, "seller": seller, "price": price, "ok": None, "message": None},
            on_conflict_do_nothing=True)
        if not claimed:
            earlier = yield tx.select("operations", (buyer, op_id))
            yield tx.rollback()
            if (earlier["item"], earlier["seller"], earlier["price"]) != (item, seller, price):
                return Reply(False, "This op_id was already used for another purchase.")
            return Reply(earlier["ok"], earlier["message"])

        # Every row read with select_for_update stays locked until commit, so the checks
        # still hold when the writes run. Locks are always taken in the same order:
        # the claim, then the item, then the two wallets sorted by name. This prevents deadlocks.
        ok, message = False, None
        if price <= 0:
            message = "The price must be positive."
        elif buyer == seller:
            message = "You cannot buy your own item."
        if message is None:
            row = yield tx.select_for_update("items", item)
            if row is None:
                message = "No such item."
            elif row["owner"] != seller:
                message = "The seller no longer owns this item."
        if message is None:
            wallets = {}
            for name in sorted((buyer, seller)):
                wallets[name] = yield tx.select_for_update("wallets", name)
            if wallets[buyer] is None or wallets[seller] is None:
                message = "Unknown player."
            elif wallets[buyer]["coins"] < price:
                message = "Not enough coins."
        if message is None:
            moved = yield tx.update("items", item, to={"owner": buyer}, add={"version": 1},
                                    where={"owner": seller, "version": row["version"]})
            paid = yield tx.update("wallets", buyer, add={"coins": -price},
                                   at_least={"coins": price})
            credited = yield tx.update("wallets", seller, add={"coins": price})
            if not (moved and paid and credited):
                yield tx.rollback()  # never commit a partial purchase
                return Reply(False, "The purchase could not be completed. Please try again.")
            ok, message = True, "Purchase completed."

        # The answer is stored in the same transaction as the purchase, so a repeat gets it back.
        stored = yield tx.update("operations", (buyer, op_id), to={"ok": ok, "message": message})
        yield tx.commit()
        return Reply(ok, message)
    except Exception:
        yield tx.rollback()
        raise
```
MODEL: claude-haiku-5-5
