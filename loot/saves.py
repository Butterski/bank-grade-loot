"""State in memory, saved one player at a time: the oldest dupe in the book.

Many game servers keep the world in memory and write each character to disk
every few minutes, or when the player logs out. A trade changes two characters
at once. If the server stops between their two saves, the disk holds one
character from before the trade and one from after it.

This file tries every order of three events - the trade, a save of the seller,
a save of the buyer - and a server stop at every point. Then it looks at what
the server loads when it comes back.

It does not use the database model. The point is exactly that there is no
transaction around the two saves.
"""

from __future__ import annotations

from collections import Counter
from itertools import permutations

TRADE = "trade"
SAVE_SELLER = "save Cartman"
SAVE_BUYER = "save Stan"
EVENTS = (TRADE, SAVE_SELLER, SAVE_BUYER)

START = {"Cartman": {"swords": 1, "coins": 0}, "Stan": {"swords": 0, "coins": 100}}
PRICE = 100

# Outcomes in which nothing was created or destroyed. A lost trade is annoying, but it is not a dupe.
CONSISTENT = ("the trade survived", "the trade never happened", "a confirmed trade was lost")


def histories() -> list[tuple[str, ...]]:
    """Every order of the events, cut off by a server stop at every position."""
    seen = {order[:stop] for order in permutations(EVENTS) for stop in range(len(EVENTS) + 1)}
    return sorted(seen, key=lambda history: (len(history), history))


def _copy(state: dict) -> dict:
    return {player: dict(character) for player, character in state.items()}


def _trade(state: dict) -> None:
    state["Cartman"]["swords"] -= 1
    state["Stan"]["swords"] += 1
    state["Stan"]["coins"] -= PRICE
    state["Cartman"]["coins"] += PRICE


def after_restart_separate_saves(history: tuple[str, ...]) -> dict:
    """The trade changes memory. Each save writes one character. A stop loses memory."""
    memory, disk = _copy(START), _copy(START)
    for event in history:
        if event == TRADE:
            _trade(memory)
        else:
            player = event.removeprefix("save ")
            disk[player] = dict(memory[player])
    return disk


def after_restart_trade_in_one_transaction(history: tuple[str, ...]) -> dict:
    """The trade writes both characters' items and coins in one transaction, then confirms.

    The periodic saves still run, but they only carry what may be lost:
    position, health, cooldowns. Items and coins never travel with them.
    """
    disk = _copy(START)
    for event in history:
        if event == TRADE:
            _trade(disk)
    return disk


def judge(history: tuple[str, ...], disk: dict) -> str:
    swords = sum(character["swords"] for character in disk.values())
    if swords > 1:
        return f"duplicate: {swords} x Sword 17"
    if swords < 1:
        return "the sword is gone"
    if disk == START:
        return "a confirmed trade was lost" if TRADE in history else "the trade never happened"
    return "the trade survived"


def outcomes(after_restart) -> Counter:
    """How many histories end in each outcome, the broken ones first."""
    counted = Counter(judge(history, after_restart(history)) for history in histories())
    return Counter(dict(sorted(counted.items(), key=lambda item: (item[0] in CONSISTENT, item[0]))))


def broken(after_restart) -> list[tuple[tuple[str, ...], str]]:
    found = []
    for history in histories():
        verdict = judge(history, after_restart(history))
        if verdict not in CONSISTENT:
            found.append((history, verdict))
    return found
