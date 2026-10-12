"""One sword, two buyers: seven ways to write the trade.

Cartman owns Sword 17 and no coins. Stan and Kenny have 100 coins each.
The price is 100 coins.

Every handler is a generator. Each `yield` is one statement sent to the
database, and therefore one place where another request can run or the
process can stop. Read the handlers top to bottom as ordinary code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .db import Database
from .explore import Handler

SELLER = "Cartman"
ITEM = 17
PRICE = 100
START = {"Cartman": 0, "Stan": 100, "Kenny": 100}


@dataclass(frozen=True)
class Reply:
    """What the server tells the client."""

    ok: bool
    text: str

    def __str__(self) -> str:
        return f"{'ok' if self.ok else 'rejected'}: {self.text}"


NAMES = {17: "Sword 17", 9: "Shield 9"}


def _owns(buyer: str, item: int) -> Reply:
    return Reply(True, f"{buyer} owns {NAMES.get(item, f'item {item}')}")


UNAVAILABLE = Reply(False, "item unavailable")
TOO_POOR = Reply(False, "not enough coins")


# --- the data ---------------------------------------------------------------


def lists_world() -> Database:
    """The weak model: an inventory list per player.

    Nothing in the database says that an item can be in only one list.
    """
    db = Database("game", inventory=("row",), wallets=("player",))
    db.load("wallets", {player: {"coins": coins} for player, coins in START.items()})
    db.add("inventory", {"player": SELLER, "item": ITEM})
    return db


def rows_world() -> Database:
    """The fixed model: one row per item with one owner column."""
    db = Database(
        "game",
        items=("id",),
        wallets=("player",),
        ledger=("entry",),
        operations=("caller", "op_id"),
    )
    db.load("items", {ITEM: {"owner": SELLER, "version": 7}})
    db.load("wallets", {player: {"coins": coins} for player, coins in START.items()})
    return db


def owners(db: Database, item: int = ITEM) -> list[str]:
    """Everybody who has the item. More than one name means a duplicate."""
    if "inventory" in db.tables:
        return sorted(row["player"] for row in db.rows("inventory") if row["item"] == item)
    row = db.row("items", item)
    return [row["owner"]] if row else []


def coins(db: Database) -> dict[str, int]:
    return {row["player"]: row["coins"] for row in db.rows("wallets")}


# --- the weak design ---------------------------------------------------------


def _buy_from_lists(q, buyer: str, seller: str, item: int, price: int) -> Handler:
    """`q` is where the statements go: the database itself, or one transaction."""
    has_it = yield q.select_where("inventory", where={"player": seller, "item": item})
    if not has_it:
        return UNAVAILABLE
    wallet = yield q.select("wallets", buyer)
    if wallet["coins"] < price:
        return TOO_POOR
    yield q.update("wallets", buyer, add={"coins": -price})
    yield q.update("wallets", seller, add={"coins": +price})
    yield q.delete_where("inventory", where={"player": seller, "item": item})  # 0 rows? nobody looks
    yield q.append("inventory", {"player": buyer, "item": item})
    return _owns(buyer, item)


def buy_naive(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """Separate statements. A stale read, an ignored row count, a model that allows two owners."""
    return (yield from _buy_from_lists(db, buyer, seller, item, price))


def buy_naive_in_transaction(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """The same statements between BEGIN and COMMIT. All or nothing, but still a stale read."""
    tx = db.begin()
    reply = yield from _buy_from_lists(tx, buyer, seller, item, price)
    yield tx.commit()
    return reply


# --- lock and check ----------------------------------------------------------


def _locked_trade(tx, op_id, buyer, seller, item, price, wallets_in_fixed_order=True) -> Handler:
    """Lock the rows, check while holding the locks, change everything. The caller commits."""
    sword = yield tx.select_for_update("items", item)
    wallet = {}
    order = sorted((buyer, seller)) if wallets_in_fixed_order else (buyer, seller)
    for player in order:
        wallet[player] = yield tx.select_for_update("wallets", player)
    if sword is None or sword["owner"] != seller:
        return UNAVAILABLE
    if wallet[buyer]["coins"] < price:
        return TOO_POOR
    yield tx.update("items", item, to={"owner": buyer}, add={"version": 1})
    yield tx.update("wallets", buyer, add={"coins": -price})
    yield tx.update("wallets", seller, add={"coins": +price})
    yield tx.append(
        "ledger",
        {"op": op_id, "account": buyer, "amount": -price},
        {"op": op_id, "account": seller, "amount": +price},
    )
    return _owns(buyer, item)


def buy_lock_and_check(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """Rule 1: protect the complete action."""
    tx = db.begin()
    reply = yield from _locked_trade(tx, op_id, buyer, seller, item, price)
    yield tx.commit()
    return reply


def buy_locking_buyer_first(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """Lock and check, but the wallets are locked in the order they appear in the request."""
    tx = db.begin()
    reply = yield from _locked_trade(tx, op_id, buyer, seller, item, price, wallets_in_fixed_order=False)
    yield tx.commit()
    return reply


# --- check at write time -----------------------------------------------------


def _buy_optimistic(db, op_id, buyer, seller, item, price, look_at_row_count) -> Handler:
    tx = db.begin()
    sword = yield tx.select("items", item)  # a plain look, no lock
    if sword is None or sword["owner"] != seller:
        yield tx.rollback()
        return UNAVAILABLE
    moved = yield tx.update(
        "items",
        item,
        to={"owner": buyer},
        add={"version": 1},
        where={"owner": seller, "version": sword["version"]},
    )
    if look_at_row_count and not moved:  # zero rows: somebody was faster
        yield tx.rollback()
        return UNAVAILABLE
    paid = yield tx.update("wallets", buyer, add={"coins": -price}, at_least={"coins": price})
    if not paid:
        yield tx.rollback()
        return TOO_POOR
    yield tx.update("wallets", seller, add={"coins": +price})
    yield tx.append(
        "ledger",
        {"op": op_id, "account": buyer, "amount": -price},
        {"op": op_id, "account": seller, "amount": +price},
    )
    yield tx.commit()
    return _owns(buyer, item)


def buy_optimistic(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """Rule 1, the other way: change the owner only if owner and version are still the same."""
    return (yield from _buy_optimistic(db, op_id, buyer, seller, item, price, look_at_row_count=True))


def buy_optimistic_rows_ignored(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """The same conditional write, but nobody looks at how many rows it changed."""
    return (yield from _buy_optimistic(db, op_id, buyer, seller, item, price, look_at_row_count=False))


# --- lock and check, plus a stored result per operation ----------------------


def buy_result_stored_last(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """Look the operation up first, store its result last.

    A retry that arrives after the first attempt is answered correctly.
    Two copies in flight both find nothing, and the second one is refused.
    """
    tx = db.begin()
    stored = yield tx.select("operations", (buyer, op_id))
    if stored:
        yield tx.commit()
        return stored["result"]
    reply = yield from _locked_trade(tx, op_id, buyer, seller, item, price)
    yield tx.insert("operations", (buyer, op_id), {"result": reply}, on_conflict_do_nothing=True)
    yield tx.commit()
    return reply


def buy_reference(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
    """Rules 1 and 2: the complete action, and the same answer for the same request."""
    tx = db.begin()
    request = {"seller": seller, "item": item, "price": price}
    mine = yield tx.insert(
        "operations", (buyer, op_id), {"request": request, "result": None}, on_conflict_do_nothing=True
    )
    if not mine:  # this operation already ran: return what it returned
        stored = yield tx.select("operations", (buyer, op_id))
        yield tx.commit()
        if stored["request"] != request:
            return Reply(False, "operation ID reused with different data")
        return stored["result"]
    reply = yield from _locked_trade(tx, op_id, buyer, seller, item, price)
    yield tx.update("operations", (buyer, op_id), to={"result": reply})
    yield tx.commit()
    return reply


# --- the list ---------------------------------------------------------------


@dataclass(frozen=True)
class TradeDesign:
    name: str
    title: str
    world: Callable[[], Database]
    buy: Callable[..., Handler]


TRADE_DESIGNS = [
    TradeDesign("naive", "separate statements, an inventory list per player", lists_world, buy_naive),
    TradeDesign(
        "naive-in-transaction", "the same statements between BEGIN and COMMIT", lists_world, buy_naive_in_transaction
    ),
    TradeDesign(
        "optimistic-rows-ignored", "conditional write, row count ignored", rows_world, buy_optimistic_rows_ignored
    ),
    TradeDesign("optimistic", "rule 1 only: check at write time, count the rows", rows_world, buy_optimistic),
    TradeDesign("lock-and-check", "rule 1 only: lock the rows, check, write", rows_world, buy_lock_and_check),
    TradeDesign(
        "result-stored-last",
        "lock and check; the operation is looked up first and its result stored last",
        rows_world,
        buy_result_stored_last,
    ),
    TradeDesign(
        "reference",
        "rules 1 and 2: the operation ID is claimed first, then lock and check",
        rows_world,
        buy_reference,
    ),
]

TRADE = {design.name: design for design in TRADE_DESIGNS}
