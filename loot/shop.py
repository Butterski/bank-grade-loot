"""One purchase for real money: Stan pays EUR 9.99 for 100 gems.

Two systems are involved, so one database transaction cannot cover the whole
story: the game stores the purchase, a separate wallet stores the gems.

The store's charge is taken as given. `PAID` is what the store's own records
say, and it is the only thing in this file that is never wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

from .db import Database, Statement
from .explore import Handler, atomically
from .trade import Reply


@dataclass(frozen=True)
class Purchase:
    provider: str
    purchase_id: str
    account: str
    gems: int

    @property
    def key(self) -> tuple[str, str]:
        return (self.provider, self.purchase_id)


@dataclass(frozen=True)
class Event:
    """A message to the wallet: grant these gems for this purchase."""

    event_id: str
    purchase: Purchase


P42 = Purchase("store", "P42", "Stan", 100)
PAID = [P42]
E42 = Event("E42", P42)

GRANTED = Reply(True, "granted")
ALREADY = Reply(True, "already granted")


# --- four wallets -----------------------------------------------------------


def grant_every_time(wallet: Database, event: Event) -> Handler:
    """Add the gems. Every message counts."""
    p = event.purchase
    tx = wallet.begin()
    yield tx.update("wallets", p.account, add={"gems": p.gems})
    yield tx.commit()
    return GRANTED


def grant_after_looking(wallet: Database, event: Event) -> Handler:
    """Look for an earlier grant, then add. It passes every single-threaded test."""
    p = event.purchase
    tx = wallet.begin()
    earlier = yield tx.select_where("grant_log", where={"purchase_id": p.purchase_id})
    if earlier:
        yield tx.commit()
        return ALREADY
    yield tx.append("grant_log", {"purchase_id": p.purchase_id, "event_id": event.event_id})
    yield tx.update("wallets", p.account, add={"gems": p.gems})
    yield tx.commit()
    return GRANTED


def grant_once_per_event(wallet: Database, event: Event) -> Handler:
    """UNIQUE (event_id): a repeated message is harmless, a new message is a new grant."""
    p = event.purchase
    tx = wallet.begin()
    new = yield tx.insert("events", event.event_id, {"purchase_id": p.purchase_id}, on_conflict_do_nothing=True)
    if not new:
        yield tx.commit()
        return ALREADY
    yield tx.update("wallets", p.account, add={"gems": p.gems})
    yield tx.commit()
    return GRANTED


def grant_once_per_purchase(wallet: Database, event: Event) -> Handler:
    """UNIQUE (provider, purchase_id): one grant per payment, whatever the message is called."""
    p = event.purchase
    tx = wallet.begin()
    new = yield tx.insert("grants", p.key, {"event_id": event.event_id, "gems": p.gems}, on_conflict_do_nothing=True)
    if not new:
        yield tx.commit()
        return ALREADY
    yield tx.update("wallets", p.account, add={"gems": p.gems})
    yield tx.commit()
    return GRANTED


@dataclass(frozen=True)
class WalletDesign:
    name: str
    title: str
    grant: Callable[[Database, Event], Handler]
    tables: dict  # extra tables this wallet keeps
    records: str | None  # the table that says which purchases were granted, if any


WALLET_DESIGNS = [
    WalletDesign("every-time", "add the gems for every message", grant_every_time, {}, None),
    WalletDesign(
        "look-first", "SELECT an earlier grant, then add (no unique rule)", grant_after_looking,
        {"grant_log": ("row",)}, "grant_log",
    ),
    WalletDesign(
        "once-per-event", "UNIQUE (event_id)", grant_once_per_event, {"events": ("event_id",)}, "events"
    ),
    WalletDesign(
        "once-per-purchase", "UNIQUE (provider, purchase_id)", grant_once_per_purchase,
        {"grants": ("provider", "purchase_id")}, "grants",
    ),
]

WALLET = {design.name: design for design in WALLET_DESIGNS}


def wallet_world(design: WalletDesign) -> Database:
    wallet = Database("wallet", wallets=("account",), **design.tables)
    wallet.load("wallets", {P42.account: {"gems": 0}})
    return wallet


def gems(wallet: Database, account: str = P42.account) -> int:
    return wallet.row("wallets", account)["gems"]


# --- the game side ----------------------------------------------------------


def shop_world(wallet_design: WalletDesign) -> SimpleNamespace:
    game = Database("game", purchases=("provider", "purchase_id"), outbox=("event_id",))
    return SimpleNamespace(game=game, wallet=wallet_world(wallet_design))


def call_wallet(world, wallet_design: WalletDesign, event: Event) -> Statement:
    """One call to the wallet service, seen from the caller as a single step.

    Stopping the caller before this step means the wallet never heard of it.
    Stopping it after means the wallet has committed. Two calls racing inside
    the wallet are covered separately, by check 05.
    """
    sql = f"CALL wallet.grant({event.event_id}, {event.purchase.purchase_id})"
    return Statement(sql, lambda: atomically(wallet_design.grant(world.wallet, event)), tuple)


def _purchase_row(p: Purchase) -> dict:
    return {"account": p.account, "gems": p.gems}


def buy_gems_save_then_call(world, wallet_design, p: Purchase = P42) -> Handler:
    """Save the purchase, then tell the wallet. Two writes, nothing holding them together."""
    new = yield world.game.insert("purchases", p.key, _purchase_row(p), on_conflict_do_nothing=True)
    if not new:
        return Reply(True, "already processed")  # a retry ends here, and nobody grants the gems
    yield call_wallet(world, wallet_design, Event("E42", p))
    return GRANTED


def buy_gems_call_then_save(world, wallet_design, p: Purchase = P42) -> Handler:
    """Tell the wallet first, then save the purchase."""
    yield call_wallet(world, wallet_design, Event("E42", p))
    yield world.game.insert("purchases", p.key, _purchase_row(p), on_conflict_do_nothing=True)
    return GRANTED


def buy_gems_with_outbox(world, wallet_design, p: Purchase = P42) -> Handler:
    """The transactional outbox: the purchase and its delivery request commit together, or not at all."""
    tx = world.game.begin()
    new = yield tx.insert("purchases", p.key, _purchase_row(p), on_conflict_do_nothing=True)
    if new:
        yield tx.insert(
            "outbox",
            "E42",
            {"provider": p.provider, "purchase_id": p.purchase_id, "account": p.account, "gems": p.gems, "sent": False},
        )
    yield tx.commit()
    return Reply(True, "purchase saved")


def publisher(world, wallet_design) -> Handler:
    """Reads the outbox and sends what was not sent. After a restart it starts from the top."""
    waiting = yield world.game.select_where("outbox", where={"sent": False})
    for row in waiting:
        purchase = Purchase(row["provider"], row["purchase_id"], row["account"], row["gems"])
        yield call_wallet(world, wallet_design, Event(row["event_id"], purchase))
        yield world.game.update("outbox", row["event_id"], to={"sent": True})
    return Reply(True, "outbox is empty")


def reconciler(world, wallet_design, paid=PAID) -> Handler:
    """Compares what the store says was paid with what the wallet says was granted."""
    granted = set()
    if wallet_design.records:
        rows = yield world.wallet.select_where(wallet_design.records)
        granted = {row["purchase_id"] for row in rows}
    for p in paid:
        if p.purchase_id in granted:
            continue
        yield world.game.insert("purchases", p.key, _purchase_row(p), on_conflict_do_nothing=True)
        # A new event ID on purpose: recovery must not depend on reusing the old message.
        yield call_wallet(world, wallet_design, Event(f"R-{p.purchase_id}", p))
    return Reply(True, "reconciled")


@dataclass(frozen=True)
class ShopDesign:
    name: str
    title: str
    buy: Callable[..., Handler]
    wallet: WalletDesign
    has_publisher: bool = False
    has_reconciler: bool = False


SHOP_DESIGNS = [
    ShopDesign("save-then-call", "save the purchase, then call the wallet", buy_gems_save_then_call,
               WALLET["every-time"]),
    ShopDesign("call-then-save", "call the wallet, then save the purchase", buy_gems_call_then_save,
               WALLET["every-time"]),
    ShopDesign("outbox", "outbox and publisher; the wallet adds gems for every message", buy_gems_with_outbox,
               WALLET["every-time"], has_publisher=True),
    ShopDesign("outbox-event-ids", "outbox, publisher, reconciler; the wallet remembers event IDs",
               buy_gems_with_outbox, WALLET["once-per-event"], has_publisher=True, has_reconciler=True),
    ShopDesign("reference", "outbox, publisher, reconciler; one grant per purchase",
               buy_gems_with_outbox, WALLET["once-per-purchase"], has_publisher=True, has_reconciler=True),
]

SHOP = {design.name: design for design in SHOP_DESIGNS}
