```python
def buy(db, op_id, buyer, seller, item, price):
    # Reject bad requests before opening a transaction. A negative price would move coins the wrong way.
    if not isinstance(price, (int, float)) or price <= 0 or buyer == seller:
        return Reply(False, "invalid purchase")

    # One transaction: the purchase either happens completely or not at all.
    tx = db.begin()

    # Claim op_id first, so a retried request cannot run twice. This needs a `purchases` table keyed by op_id.
    # If another open transaction holds the same op_id, this insert waits for it. Then it raises
    # UniqueViolation if that attempt committed, or succeeds if it rolled back and this request must run.
    try:
        yield tx.insert("purchases", op_id,
                        {"buyer": buyer, "seller": seller, "item": item, "price": price})
    except UniqueViolation:
        yield tx.rollback()
        done = yield db.select("purchases", op_id)  # plain select: sees the committed row
        wanted = (buyer, seller, item, price)
        same = done is not None and (done["buyer"], done["seller"], done["item"], done["price"]) == wanted
        return Reply(same, "already done" if same else "op_id already used for another purchase")

    # Lock order is always: claim, then item, then wallets (by name). This order rules out deadlocks.
    # The conditional update locks the item and checks the owner after any concurrent sale has finished,
    # so one item cannot be sold twice. 0 means the item is missing or not owned by the seller.
    moved = yield tx.update("items", item, to={"owner": buyer}, add={"version": 1}, where={"owner": seller})
    if moved != 1:
        yield tx.rollback()
        return Reply(False, "item does not exist or is not owned by the seller")

    # The debit applies only if the buyer still has enough coins, measured on the latest balance.
    # 0 means not enough coins, or a wallet is missing.
    changes = {
        buyer: {"add": {"coins": -price}, "at_least": {"coins": price}},
        seller: {"add": {"coins": price}},
    }
    for player in sorted(changes):
        changed = yield tx.update("wallets", player, **changes[player])
        if changed != 1:
            yield tx.rollback()
            return Reply(False, "not enough coins, or a wallet is missing")

    yield tx.commit()
    return Reply(True, f"{buyer} bought item {item} from {seller} for {price} coins")
```
MODEL: claude-haiku-5-5
