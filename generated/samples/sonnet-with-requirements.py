def buy(db, op_id, buyer, seller, item, price):
    # Requests that can never be a valid purchase: nothing to lock, nothing to record.
    if not price > 0:
        return Reply(False, "The price must be greater than zero.")
    if buyer == seller:
        return Reply(False, "You cannot buy your own item.")

    key = (buyer, op_id)
    tx = db.begin()

    # 1. Claim the operation id inside the SAME transaction as the purchase.
    #    A duplicate running at the same moment waits on this insert until we
    #    commit or roll back, so it can never buy a second time. If we crash
    #    before the commit, the claim vanishes together with everything else.
    try:
        claimed = yield tx.insert(
            "operations",
            key,
            {"caller": buyer, "op_id": op_id, "ok": None, "message": None},
            on_conflict_do_nothing=True,
        )
    except UniqueViolation:  # tolerate a driver that raises instead of returning 0
        claimed = 0

    if not claimed:
        # Duplicate request: the first one is committed, so replay its stored answer.
        yield tx.rollback()
        done = yield db.select("operations", key)
        if done is None:
            return Reply(False, "This operation is still in progress, try again.")
        return Reply(done["ok"], done["message"])

    ok = False
    message = None

    # 2. Lock the item, then the wallets, and only then decide. The lock order is
    #    always: operation, item, wallets sorted by name, so A buying from B while
    #    B buys from A cannot deadlock. All checks happen before any write.
    row = yield tx.select_for_update("items", item)
    if row is None or row["owner"] != seller:
        message = "The item is not available from this seller."
    else:
        wallets = {}
        for player in sorted((buyer, seller)):
            wallets[player] = yield tx.select_for_update("wallets", player)

        if wallets[buyer] is None or wallets[seller] is None:
            message = "Unknown player."
        elif wallets[buyer]["coins"] < price:
            message = "Not enough coins."
        else:
            moved = yield tx.update(
                "items", item,
                to={"owner": buyer}, add={"version": 1}, where={"owner": seller},
            )
            paid = yield tx.update(
                "wallets", buyer,
                add={"coins": -price}, at_least={"coins": price},
            )
            credited = yield tx.update("wallets", seller, add={"coins": price})
            if moved != 1 or paid != 1 or credited != 1:
                yield tx.rollback()
                return Reply(False, "The purchase could not be completed.")
            ok = True
            message = "Bought item %s from %s for %s coins." % (item, seller, price)

    # 3. Store the answer (success or failure) and commit everything at once.
    yield tx.update("operations", key, to={"ok": ok, "message": message})
    yield tx.commit()
    return Reply(ok, message)
