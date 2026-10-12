def buy(db, op_id, buyer, seller, item, price):
    # Refusals that depend on the request alone. A repeated request fails the
    # same way again, so nothing has to be remembered for them.
    if buyer == seller:
        return Reply(False, "You cannot buy your own item.")
    if not isinstance(price, (int, float)) or price < 0:
        return Reply(False, "The price must be zero or more coins.")

    tx = db.begin()
    try:
        # 1. Claim (buyer, op_id) as the very first statement. A second copy of
        #    the same request blocks on this insert, holding no other lock,
        #    until this transaction has ended: if we committed, it gets 0 and
        #    repeats our stored answer (see the end); if we rolled back, it
        #    gets 1 and does the purchase itself.
        claimed = yield tx.insert(
            "operations",
            (buyer, op_id),
            {"caller": buyer, "op_id": op_id, "item": item, "seller": seller, "price": price},
            on_conflict_do_nothing=True,
        )
        if claimed:
            # 2. Lock every row we are going to touch, always in the same order
            #    (the item, then the wallets by player name), so two purchases
            #    can never wait for each other in a circle. A purchase of the
            #    same item, or another purchase by the same buyer, waits here
            #    until the earlier transaction has ended, then sees its result.
            item_row = yield tx.select_for_update("items", item)
            wallets = {}
            for player in sorted((buyer, seller)):
                wallets[player] = yield tx.select_for_update("wallets", player)

            # 3. Decide. Nothing has been changed so far, so a refusal is just
            #    an answer to store; there is nothing to undo.
            if item_row is None:
                ok, message = False, "That item does not exist."
            elif item_row["owner"] != seller:
                ok, message = False, "That item does not belong to %s." % seller
            elif wallets[buyer] is None:
                ok, message = False, "You do not have a wallet."
            elif wallets[seller] is None:
                ok, message = False, "%s does not have a wallet." % seller
            else:
                # 4. Take the coins first. The balance check is part of the
                #    statement itself, so the buyer can never go below zero,
                #    and when it fails nothing else has been touched yet.
                paid = yield tx.update(
                    "wallets", buyer, add={"coins": -price}, at_least={"coins": price}
                )
                if not paid:
                    ok, message = False, "You do not have enough coins."
                else:
                    yield tx.update("wallets", seller, add={"coins": price})
                    yield tx.update("items", item, to={"owner": buyer}, add={"version": 1})
                    ok = True
                    message = "You bought item %s from %s for %s coins." % (item, seller, price)

            # 5. Store the answer and commit it in one piece with the purchase.
            #    Until here the database holds nothing of this request: if the
            #    process dies between any two statements, the transaction, the
            #    claim included, is thrown away, and a retry starts from scratch.
            yield tx.update("operations", (buyer, op_id), to={"ok": ok, "message": message})
            yield tx.commit()
            return Reply(ok, message)
    except Exception:
        yield tx.rollback()
        raise

    # The claim belongs to another copy of this request, and that copy has
    # committed its answer (our insert returned only after it had). Repeat it;
    # a plain select outside the transaction returns the committed row.
    yield tx.rollback()
    done = yield db.select("operations", (buyer, op_id))
    return Reply(done["ok"], done["message"])
