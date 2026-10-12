```python
def buy(db, op_id, buyer, seller, item, price):
    """Sell `item` from `seller` to `buyer` for `price` coins: once, or not at all.

    The item, both wallets and the answer are written in one transaction, and
    the answer is stored under the unique key (buyer, op_id).

    1. Two buyers, one item: the item row is locked first. The second buyer
       waits, then sees the new owner and is refused before any coins move.
    2. One request, sent twice: only one of the two can insert the key. The
       other one keeps nothing of its own work and repeats the stored answer.
    3. A crash: before the commit nothing is kept, after it everything is.
    4. Two purchases, one wallet: the wallet is locked before the balance is
       read, so the second purchase sees what the first one left.
    """
    key = (buyer, op_id)

    # Answered before? Repeat that answer and touch nothing. This is only a
    # shortcut: the insert below is what makes the answer unique.
    answered = yield db.select("operations", key)
    if answered is not None:
        return Reply(answered["ok"], answered["message"])

    tx = db.begin()

    # Decide. Every row the decision depends on is read with a lock that is
    # held until the transaction ends. The locks are always taken in the same
    # order (the item, then the wallets by player name), so purchases that
    # cross each other wait instead of deadlocking.
    problem = None
    if buyer == seller:
        problem = "You cannot buy your own item."
    elif not (isinstance(price, (int, float)) and price >= 0):
        problem = "The price is not valid."

    if problem is None:
        item_row = yield tx.select_for_update("items", item)
        if item_row is None:
            problem = "This item does not exist."
        elif item_row["owner"] != seller:
            problem = "The seller does not own this item."

    if problem is None:
        wallets = {}
        for player in sorted((buyer, seller)):
            wallets[player] = yield tx.select_for_update("wallets", player)
        if wallets[buyer] is None or wallets[seller] is None:
            problem = "The buyer or the seller has no wallet."
        elif wallets[buyer]["coins"] < price:
            problem = "You do not have enough coins."

    ok = problem is None
    message = "You bought the item." if ok else problem

    # Store the answer in the same transaction as the purchase. A refusal is
    # an answer too: a repeat of this request gets the same refusal, and a new
    # attempt needs a new op_id.
    inserted = yield tx.insert(
        "operations", key, {"ok": ok, "message": message},
        on_conflict_do_nothing=True,
    )
    if not inserted:
        # The same request was answered while this one was working.
        # Keep nothing of this attempt and repeat that answer.
        yield tx.rollback()
        answered = yield db.select("operations", key)
        return Reply(answered["ok"], answered["message"])

    if ok:
        # The locks already guarantee these conditions. They are repeated in
        # the writes so that the database itself refuses a double sale or an
        # overdraft.
        sold = yield tx.update(
            "items", item, to={"owner": buyer}, add={"version": 1},
            where={"owner": seller, "version": item_row["version"]},
        )
        paid = yield tx.update(
            "wallets", buyer, add={"coins": -price}, at_least={"coins": price},
        )
        earned = yield tx.update("wallets", seller, add={"coins": price})
        if not (sold and paid and earned):
            # Nothing is kept and nothing is stored, so a retry is safe.
            yield tx.rollback()
            return Reply(False, "The purchase did not go through. Please try again.")

    yield tx.commit()
    return Reply(ok, message)
```
MODEL: claude-opus-5-5
