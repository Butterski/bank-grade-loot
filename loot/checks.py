"""The six checks from the talk, run against every design.

Each check builds a small scenario, lets the explorer try every ordering,
and judges the final state of each one. A check "holds" for a design only if
no ordering breaks it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Callable

from .db import Database
from .explore import Process, Run, Scenario, explore
from .shop import E42, P42, Event, ShopDesign, WalletDesign, gems, publisher, reconciler, shop_world, wallet_world
from .trade import (
    ITEM,
    PRICE,
    SELLER,
    START,
    TradeDesign,
    buy_lock_and_check,
    buy_locking_buyer_first,
    coins,
    owners,
)


@dataclass
class Verdict:
    check: str
    design: str
    orderings: int
    broken: Counter  # problem -> number of orderings that end with it
    examples: dict[str, Run]  # problem -> the broken ordering that is easiest to read

    @property
    def holds(self) -> bool:
        return not self.broken

    @property
    def broken_total(self) -> int:
        return sum(self.broken.values())

    def example(self, problem_contains: str = "") -> Run | None:
        """An ordering that ends with the most common problem, or with one that contains the given text."""
        for problem, _ in self.broken.most_common():
            if problem_contains.lower() in problem.lower():
                return self.examples[problem]
        return None


Judge = Callable[[Run], "str | None"]
Case = tuple[Scenario, Judge, int]  # the scenario, its judge, and how many stops to try


def judge_all(check: str, design: str, cases: list[Case]) -> Verdict:
    """Explore every case and collect what broke."""
    orderings, broken, examples = 0, Counter(), {}
    for scenario, judge, max_crashes in cases:
        for run in explore(scenario, max_crashes):
            orderings += 1
            problem = judge(run)
            if not problem:
                continue
            broken[problem] += 1
            best = examples.get(problem)
            if best is None or (run.switches, len(run.trace)) < (best.switches, len(best.trace)):
                run.problem = problem
                examples[problem] = run
    return Verdict(check, design, orderings, broken, examples)


# --- what a correct trade looks like ----------------------------------------


def trade_problem(db, allowed_owners: set[str]) -> str | None:
    """The four trade rules from the talk, checked on the final state."""
    who, balance = owners(db), coins(db)
    if len(who) > 1:
        return f"duplicate: {len(who)} x Sword {ITEM}"
    if not who:
        return "the sword is gone"
    if min(balance.values()) < 0:
        return "negative balance"
    if sum(balance.values()) != sum(START.values()):
        return "coins created or destroyed"
    holder = who[0]
    paid = [player for player in balance if player != SELLER and balance[player] < START[player]]
    if holder == SELLER and paid:
        return "paid, no sword"
    if holder != SELLER and holder not in paid:
        return "sword without payment"
    if holder != SELLER and len(paid) > 1:
        return "paid, no sword"
    if holder not in allowed_owners:
        return "nobody got the sword" if holder == SELLER else f"{holder} got the sword"
    if "ledger" in db.tables:
        entries = db.rows("ledger")
        moved = {} if holder == SELLER else {holder: -PRICE, SELLER: +PRICE}
        booked: dict[str, int] = {}
        for entry in entries:
            booked[entry["account"]] = booked.get(entry["account"], 0) + entry["amount"]
        if len({entry["op"] for entry in entries}) > 1:
            return "a second transfer"
        if booked != moved:
            return "the ledger does not match the balances"
    return None


# --- 01 ----------------------------------------------------------------------


def check_01_two_buyers(design: TradeDesign) -> Verdict:
    """Two buyers compete: one owner, one payment, the other buyer keeps his coins."""

    def scenario():
        db = design.world()
        return db, [
            Process("Stan", lambda: design.buy(db, "T42", "Stan")),
            Process("Kenny", lambda: design.buy(db, "T43", "Kenny")),
        ]

    def judge(run: Run) -> str | None:
        if run.stuck:
            return "deadlock"
        problem = trade_problem(run.world, {"Stan", "Kenny"})
        if problem:
            return problem
        winner = owners(run.world)[0]
        loser = "Kenny" if winner == "Stan" else "Stan"
        if not run.reply(winner).ok or run.reply(loser).ok:
            return "wrong answer"
        return None

    return judge_all("01", design.name, [(scenario, judge, 0)])


# --- 02 ----------------------------------------------------------------------


def check_02_same_request_twice(design: TradeDesign) -> Verdict:
    """The same request arrives twice: the stored result, no second transfer."""

    def scenario():
        db = design.world()
        return db, [
            Process("Stan", lambda: design.buy(db, "T42", "Stan")),
            Process("Stan again", lambda: design.buy(db, "T42", "Stan")),
        ]

    def judge(run: Run) -> str | None:
        if run.stuck:
            return "deadlock"
        problem = trade_problem(run.world, {"Stan"})
        if problem:
            return problem
        first, second = run.reply("Stan"), run.reply("Stan again")
        if first is None or second is None or not (first.ok and second.ok):
            return "wrong answer to the repeated request"
        return None

    return judge_all("02", design.name, [(scenario, judge, 0)])


# --- 03 ----------------------------------------------------------------------


def check_03_stops_before_commit(design: TradeDesign) -> Verdict:
    """The process stops before it is done: no partial trade."""

    def scenario():
        db = design.world()
        return db, [Process("Stan", lambda: design.buy(db, "T42", "Stan"), crashes=True)]

    def judge(run: Run) -> str | None:
        return trade_problem(run.world, {SELLER, "Stan"})

    return judge_all("03", design.name, [(scenario, judge, 1)])


# --- 04 ----------------------------------------------------------------------


def check_04_reply_lost(design: TradeDesign) -> Verdict:
    """The work may be done but the reply never arrives: the client asks again.

    The first attempt stops at any point, or finishes and loses its reply.
    Then the same request is sent again.
    """

    def scenario():
        db = design.world()
        return db, [
            Process("Stan", lambda: design.buy(db, "T42", "Stan"), crashes=True),
            Process("Stan again", lambda: design.buy(db, "T42", "Stan"), after=lambda run: run.ended("Stan")),
        ]

    def judge(run: Run) -> str | None:
        problem = trade_problem(run.world, {"Stan"})
        if problem:
            return problem
        reply = run.reply("Stan again")
        if reply is None or not reply.ok:
            return "wrong answer to the repeated request"
        return None

    return judge_all("04", design.name, [(scenario, judge, 1)])


# --- 05 ----------------------------------------------------------------------


def check_05_event_arrives_twice(design: WalletDesign) -> Verdict:
    """A paid event arrives twice: one grant per purchase.

    Two cases: the very same event twice, and the same purchase announced
    under a new event ID (a second sender, such as a reconciler).
    """

    def case(second: Event, label: str):
        def scenario():
            wallet = wallet_world(design)
            return wallet, [
                Process("delivery 1", lambda: design.grant(wallet, E42)),
                Process("delivery 2", lambda: design.grant(wallet, second)),
            ]

        def judge(run: Run) -> str | None:
            if run.stuck:
                return "deadlock"
            granted = gems(run.world)
            return None if granted == P42.gems else f"{granted} gems, {label}"

        return scenario, judge, 0

    return judge_all(
        "05",
        design.name,
        [case(E42, "same event twice"), case(Event("R-P42", P42), "same purchase under a new event ID")],
    )


# --- 06 ----------------------------------------------------------------------


def check_06_payment_without_grant(design: ShopDesign, max_crashes: int = 2) -> Verdict:
    """A payment has no grant: find it and deliver it once.

    The whole purchase is run with stops at every point. The store's charge
    has already happened. When everything has settled, the paid purchase must
    have exactly one grant, with or without a retry from the client.
    """

    def case(client_retries: bool):
        def scenario():
            world = shop_world(design.wallet)
            requests = ["server"] + (["server (client retry)"] if client_retries else [])
            processes = [Process("server", lambda: design.buy(world, design.wallet), crashes=True)]
            if client_retries:
                processes.append(
                    Process(
                        "server (client retry)",
                        lambda: design.buy(world, design.wallet),
                        after=lambda run: run.ended("server"),
                    )
                )
            if design.has_publisher:
                processes.append(
                    Process(
                        "publisher",
                        lambda: publisher(world, design.wallet),
                        after=lambda run: all(run.ended(name) for name in requests),
                        crashes=True,
                        restarts=True,
                    )
                )
            if design.has_reconciler:
                processes.append(
                    Process(
                        "reconciler",
                        lambda: reconciler(world, design.wallet),
                        after=lambda run: run.ended("server"),
                        crashes=True,
                        restarts=True,
                    )
                )
            return world, processes

        def judge(run: Run) -> str | None:
            if run.stuck:
                return "deadlock"
            granted = gems(run.world.wallet)
            if granted == 0:
                return "paid, no gems"
            if granted != P42.gems:
                return f"{granted} gems for one payment"
            if run.world.game.row("purchases", P42.key) is None:
                return "gems, but no purchase record"
            return None

        return scenario, judge, max_crashes

    return judge_all("06", design.name, [case(client_retries=True), case(client_retries=False)])


# --- a side note: why the locks are taken in a fixed order --------------------


def lock_order(wallets_in_fixed_order: bool) -> Verdict:
    """Two trades between the same two players, in opposite directions."""
    buy = buy_lock_and_check if wallets_in_fixed_order else buy_locking_buyer_first

    def scenario():
        db = Database("game", items=("id",), wallets=("player",), ledger=("entry",), operations=("caller", "op_id"))
        db.load("items", {17: {"owner": "Cartman", "version": 7}, 9: {"owner": "Stan", "version": 3}})
        db.load("wallets", {"Cartman": {"coins": 100}, "Stan": {"coins": 100}})
        return db, [
            Process("Stan buys Sword 17", lambda: buy(db, "T42", "Stan", seller="Cartman", item=17)),
            Process("Cartman buys Shield 9", lambda: buy(db, "T44", "Cartman", seller="Stan", item=9)),
        ]

    def judge(run: Run) -> str | None:
        return "deadlock" if run.stuck else None

    name = "fixed order" if wallets_in_fixed_order else "buyer first"
    return judge_all("lock order", name, [(scenario, judge, 0)])


# --- a side note: one wallet, two purchases -----------------------------------


def double_spend(design: TradeDesign) -> Verdict:
    """Stan has 100 coins and buys two items for 100 each, from two sellers, at the same moment.

    The six checks never spend the same coins twice. This scenario does.
    """
    shield, shield_seller = 9, "Kenny"

    def scenario():
        db = design.world()
        if "inventory" in db.tables:
            db.add("inventory", {"player": shield_seller, "item": shield})
        else:
            db.load("items", {shield: {"owner": shield_seller, "version": 3}})
        return db, [
            Process("Stan buys Sword 17", lambda: design.buy(db, "T42", "Stan", seller=SELLER, item=ITEM)),
            Process("Stan buys Shield 9", lambda: design.buy(db, "T45", "Stan", seller=shield_seller, item=shield)),
        ]

    def judge(run: Run) -> str | None:
        if run.stuck:
            return "deadlock"
        balance = coins(run.world)
        if min(balance.values()) < 0:
            return "negative balance"
        if sum(balance.values()) != sum(START.values()):
            return "coins created or destroyed"
        holders = {ITEM: owners(run.world, ITEM), shield: owners(run.world, shield)}
        if any(len(who) != 1 for who in holders.values()):
            return "an item was duplicated or lost"
        bought = [item for item, who in holders.items() if who == ["Stan"]]
        if len(bought) == 2:
            return "two items for 100 coins"
        if not bought:
            return "nobody was served"
        paid_seller = SELLER if bought[0] == ITEM else shield_seller
        expected = {**START, "Stan": START["Stan"] - PRICE, paid_seller: START[paid_seller] + PRICE}
        if balance != expected:
            return "the coins went to the wrong place"
        replies = {ITEM: run.reply("Stan buys Sword 17"), shield: run.reply("Stan buys Shield 9")}
        if any(replies[item].ok != (item in bought) for item in replies):
            return "wrong answer"
        return None

    return judge_all("double spend", design.name, [(scenario, judge, 0)])


CHECKS = {
    "01": ("Two buyers compete", check_01_two_buyers),
    "02": ("The same request arrives twice", check_02_same_request_twice),
    "03": ("The process stops before commit", check_03_stops_before_commit),
    "04": ("The reply is lost and the client asks again", check_04_reply_lost),
    "05": ("A paid event arrives twice", check_05_event_arrives_twice),
    "06": ("A payment has no grant", check_06_payment_without_grant),
}
