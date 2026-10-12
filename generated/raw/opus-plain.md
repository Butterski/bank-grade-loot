```python
def buy(db, op_id, buyer, seller, item, price):
    """`buyer` buys `item` from `seller` for `price` coins.

    All of it happens or none of it, and at most once per op_id: also when the
    client repeats the request or when the server stops halfway.

    Needs one more table, "purchases": key = op_id, one row per completed
    purchase. A retry must reuse its op_id; a new purchase must get a new one.
    Does not check that `seller` agreed to this price (no table records offers):
    the caller must, and must not trust a price sent by the buyer's client.
    """
    if op_id is None:
        return Reply(False, "the request has no op_id")
    if not isinstance(price, int) or isinstance(price, bool) or price < 0:
        return Reply(False, "the price must be a whole number of coins, 0 or more")
    if buyer == seller:
        return Reply(False, "you cannot buy your own item")

    purchase = {"buyer": buyer, "seller": seller, "item": item, "price": price}
    bought = Reply(True, f"{buyer} bought item {item} from {seller} for {price} coins")

    # One transaction: the purchase record, the item and both wallets change
    # together or not at all.
    tx = db.begin()
    try:
        # Claim the op_id first. If the same request is being handled right now,
        # this waits for it; 0 means that it has already been completed.
        # A refused purchase is rolled back, so its op_id can be tried again.
        claimed = yield tx.insert(
            "purchases", op_id, purchase, on_conflict_do_nothing=True)
        if not claimed:
            earlier = yield tx.select("purchases", op_id)
            yield tx.rollback()
            same = earlier is not None and all(
                earlier.get(name) == value for name, value in purchase.items())
            if same:
                return bought  # a repeat: report the purchase, do not make it again
            return Reply(False, "this op_id was already used for another purchase")

        # Lock each row before checking it: a plain select can be out of date.
        # Always in the same order (the item, then the wallets by name), so that
        # two purchases can never block each other forever.
        item_row = yield tx.select_for_update("items", item)
        if item_row is None or item_row["owner"] != seller:
            yield tx.rollback()
            return Reply(False, f"{seller} does not own item {item}")

        wallet = {}
        for player in sorted([buyer, seller]):
            wallet[player] = yield tx.select_for_update("wallets", player)
        if wallet[buyer] is None:
            problem = f"{buyer} has no wallet"
        elif wallet[seller] is None:
            problem = f"{seller} has no wallet"
        elif wallet[buyer]["coins"] < price:
            problem = f"{buyer} does not have {price} coins"
        else:
            problem = None
        if problem:
            yield tx.rollback()
            return Reply(False, problem)

        # The conditions make the database check the same things once more.
        # With the rows locked they cannot fail; if one does, nothing is kept.
        moved = yield tx.update(
            "items", item, to={"owner": buyer}, add={"version": 1},
            where={"owner": seller})
        paid = yield tx.update(
            "wallets", buyer, add={"coins": -price}, at_least={"coins": price})
        earned = yield tx.update("wallets", seller, add={"coins": price})
        if not (moved == paid == earned == 1):
            yield tx.rollback()
            return Reply(False, "the purchase could not be completed, nothing changed")

        yield tx.commit()
        return bought
    except Exception:
        # A database error: leave no open transaction behind and pass the error
        # on. The client can safely send the request again with the same op_id.
        yield tx.rollback()
        raise
```
MODEL: claude-opus-5-5
