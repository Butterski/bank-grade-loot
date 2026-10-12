def buy(db, op_id, buyer, seller, item, price):
    # op_id: nothing in the database records which operations were already
    # handled, so a retried request cannot be told apart from a new one.
    # A retry is still harmless: once the first attempt went through, the
    # item belongs to the buyer and not to the seller, so the checks below
    # refuse it and nobody is charged twice. To answer a retry with the
    # reply of the first attempt, add a table keyed by op_id and insert the
    # op into it inside the transaction (a duplicate then raises
    # UniqueViolation).
    if buyer == seller:
        return Reply(False, "you cannot buy from yourself")
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not (price >= 0):
        return Reply(False, "the price must be a number of coins, zero or more")

    tx = db.begin()
    try:
        # Lock the item first, then the two wallets in name order. Every
        # purchase takes its locks in this order, so two purchases that
        # touch the same rows wait for each other instead of deadlocking.
        row = yield tx.select_for_update("items", item)
        if row is None:
            yield tx.rollback()
            return Reply(False, "there is no such item")
        if row["owner"] == buyer:
            yield tx.rollback()
            return Reply(False, "you already own this item")
        if row["owner"] != seller:
            yield tx.rollback()
            return Reply(False, "the seller does not own this item")
        version = row["version"]

        wallets = {}
        for player in sorted((buyer, seller)):
            wallets[player] = yield tx.select_for_update("wallets", player)
        if wallets[buyer] is None:
            yield tx.rollback()
            return Reply(False, "you have no wallet")
        if wallets[seller] is None:
            yield tx.rollback()
            return Reply(False, "the seller has no wallet")
        if wallets[buyer]["coins"] < price:
            yield tx.rollback()
            return Reply(False, "you do not have enough coins")

        # The rows are locked, so these conditions cannot fail any more;
        # they only guard against a mistake in the checks above.
        paid = yield tx.update("wallets", buyer, add={"coins": -price}, at_least={"coins": price})
        received = yield tx.update("wallets", seller, add={"coins": price})
        moved = yield tx.update(
            "items", item,
            to={"owner": buyer, "version": version + 1},
            where={"owner": seller, "version": version},
        )
        if paid != 1 or received != 1 or moved != 1:
            yield tx.rollback()
            return Reply(False, "the purchase could not be completed; nothing was charged")

        yield tx.commit()
    except Exception:
        yield tx.rollback()
        raise
    return Reply(True, f"{buyer} bought item {item} from {seller} for {price} coins")
