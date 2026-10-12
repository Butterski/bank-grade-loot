"""Turns verdicts into text. Plain ASCII, so it prints the same on every terminal."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from . import numbers, saves
from .checks import (
    CHECKS,
    Verdict,
    check_01_two_buyers,
    check_02_same_request_twice,
    check_03_stops_before_commit,
    check_04_reply_lost,
    check_05_event_arrives_twice,
    check_06_payment_without_grant,
    double_spend,
    lock_order,
)
from .db import Database
from .explore import Run
from .shop import SHOP, SHOP_DESIGNS, WALLET, WALLET_DESIGNS
from .trade import ITEM, TRADE, TRADE_DESIGNS, coins, owners

TRADE_CHECKS = {
    "01": check_01_two_buyers,
    "02": check_02_same_request_twice,
    "03": check_03_stops_before_commit,
    "04": check_04_reply_lost,
}

INTRO = {
    "01": "Stan and Kenny buy Sword 17 at the same moment. Every ordering of their statements.",
    "02": "Stan's request T42 is sent twice and both copies are in flight. Every ordering.",
    "03": "Stan's request stops after each statement in turn. Nobody retries.",
    "04": "Stan's request stops after each statement in turn, or finishes and loses its reply. Then T42 is sent again.",
    "05": "The wallet is told twice, at the same moment, to grant purchase P42: the same event, or a new event ID.",
    "06": "The whole purchase with stops at every point, with and without a retry from the client.",
}


def designs_for(check: str) -> list:
    if check in TRADE_CHECKS:
        return TRADE_DESIGNS
    return WALLET_DESIGNS if check == "05" else SHOP_DESIGNS


@lru_cache(maxsize=None)
def run_check(check: str, design_name: str) -> Verdict:
    if check in TRADE_CHECKS:
        return TRADE_CHECKS[check](TRADE[design_name])
    if check == "05":
        return check_05_event_arrives_twice(WALLET[design_name])
    return check_06_payment_without_grant(SHOP[design_name])


def all_verdicts() -> dict[str, list[Verdict]]:
    return {check: [run_check(check, design.name) for design in designs_for(check)] for check in CHECKS}


# --- tables -------------------------------------------------------------------


def _reasons(verdict: Verdict) -> str:
    return "; ".join(f"{problem} ({count})" for problem, count in verdict.broken.most_common())


def _rows(verdicts: list[Verdict]) -> list[str]:
    lines = [f"  {'design':<26}{'orderings':>10}{'broken':>8}   result"]
    for verdict in verdicts:
        result = "holds" if verdict.holds else f"FAILS  {_reasons(verdict)}"
        lines.append(f"  {verdict.design:<26}{verdict.orderings:>10}{verdict.broken_total:>8}   {result}")
    return lines


def check_table(check: str, verdicts: list[Verdict]) -> str:
    return "\n".join([f"Check {check} - {CHECKS[check][0]}", f"  {INTRO[check]}", ""] + _rows(verdicts))


def double_spend_text() -> str:
    lines = ["One wallet, two purchases (not one of the six checks)"]
    lines += ["  Stan has 100 coins and buys Sword 17 and Shield 9 at the same moment, 100 coins each.", ""]
    return "\n".join(lines + _rows([double_spend(design) for design in TRADE_DESIGNS]))


def summary_table(verdicts: dict[str, list[Verdict]], checks: list[str], designs: list) -> str:
    lines = ["  " + f"{'design':<26}" + "".join(f"{check:>7}" for check in checks)]
    for design in designs:
        cells = []
        for check in checks:
            verdict = next(v for v in verdicts[check] if v.design == design.name)
            cells.append(f"{'ok' if verdict.holds else 'FAILS':>7}")
        lines.append("  " + f"{design.name:<26}" + "".join(cells))
    return "\n".join(lines)


def design_list(designs: list) -> str:
    return "\n".join(f"  {design.name:<26}{design.title}" for design in designs)


def markdown_matrix() -> str:
    """The trade designs against checks 01 to 04 and the double spend, as the Markdown table in the README."""
    yes, no = "\u2705", "\u274c"
    short = {"01": "Two buyers", "02": "Sent twice", "03": "Stops early", "04": "Reply lost"}
    columns = [f"{check}<br>{short[check]}" for check in TRADE_CHECKS] + ["One wallet,<br>two purchases"]
    lines = ["| Design | " + " | ".join(columns) + " |", "|---|" + ":-:|" * len(columns)]
    for design in TRADE_DESIGNS:
        verdicts = [run(design) for run in TRADE_CHECKS.values()] + [double_spend(design)]
        lines.append(f"| `{design.name}` | " + " | ".join(yes if verdict.holds else no for verdict in verdicts) + " |")
    return "\n".join(lines)


def full_report() -> str:
    verdicts = all_verdicts()
    out = ["Bank-Grade Loot: every ordering, every stop", ""]
    out += ["THE SWORD", "Cartman sells Sword 17 for 100 coins. Stan and Kenny have 100 coins each.", ""]
    out += [design_list(TRADE_DESIGNS), ""]
    for check in TRADE_CHECKS:
        out += [check_table(check, verdicts[check]), ""]
    out += [summary_table(verdicts, list(TRADE_CHECKS), TRADE_DESIGNS), ""]
    out += [double_spend_text(), "", lock_order_text(), ""]
    out += ["THE GEMS"]
    out += ["Stan pays EUR 9.99 for 100 gems. The game saves the purchase, a separate wallet holds the gems.", ""]
    out += [design_list(WALLET_DESIGNS), "", check_table("05", verdicts["05"]), ""]
    out += [design_list(SHOP_DESIGNS), "", check_table("06", verdicts["06"]), ""]
    out += ["THE SERVER'S MEMORY", saves_text(), ""]
    out += ["Show one broken ordering:   python -m loot show 01 naive"]
    out += ["Play with the arithmetic:   python -m loot numbers"]
    return "\n".join(out)


def lock_order_text() -> str:
    lines = ["Lock order: Stan buys Sword 17 from Cartman while Cartman buys Shield 9 from Stan."]
    for fixed in (False, True):
        verdict = lock_order(fixed)
        what = "always in the same order" if fixed else "buyer first, then seller"
        lines.append(
            f"  wallets locked {what + ':':<27}{verdict.broken_total:>2} of {verdict.orderings} orderings"
            " end in a deadlock"
        )
    return "\n".join(lines)


# --- one broken ordering, step by step ---------------------------------------


def _result(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value} row" if value == 1 else f"{value} rows"
    if isinstance(value, list):
        return f"{len(value)} row" if len(value) == 1 else f"{len(value)} rows"
    if isinstance(value, dict):
        return ", ".join(f"{column}={cell}" for column, cell in value.items() if not isinstance(cell, dict))
    return str(value)


def _trade_state(db: Database) -> str:
    holders = owners(db)
    parts = []
    for player, balance in coins(db).items():
        swords = holders.count(player)
        has = "" if not swords else f", Sword {ITEM}" if swords == 1 else f", {swords} x Sword {ITEM}"
        parts.append(f"{player}: {balance} coins{has}")
    return " | ".join(parts)


def _state(world: Any) -> str:
    if isinstance(world, Database):
        if "coins" in next(iter(world.tables["wallets"].values())):
            return _trade_state(world)
        return f"Stan's wallet: {world.row('wallets', 'Stan')['gems']} gems"
    purchases = len(world.game.rows("purchases"))
    stan = world.wallet.row("wallets", "Stan")["gems"]
    return f"Stan's wallet: {stan} gems | purchases saved by the game: {purchases}"


def trace_text(run: Run) -> str:
    who_width = max(len(step.who) for step in run.trace)
    sql_width = min(96, max(len(step.sql) for step in run.trace))
    lines = []
    for step in run.trace:
        result = "no row" if step.result is None and step.sql.startswith("SELECT") else _result(step.result)
        arrow = f"  -> {result}" if result else ""
        lines.append(f"  {step.who:<{who_width}}  {step.sql:<{sql_width}}{arrow}".rstrip())
    return "\n".join(lines)


def show(check: str, design_name: str, problem_contains: str = "") -> str:
    verdict = run_check(check, design_name)
    head = f'Check {check} ({CHECKS[check][0]}), design "{design_name}"'
    if verdict.holds:
        return f"{head}: holds in all {verdict.orderings} orderings. Nothing to show."
    run = verdict.example(problem_contains)
    if run is None:
        return f'{head}: no broken ordering mentions "{problem_contains}". It breaks with: {_reasons(verdict)}'
    count = verdict.broken[run.problem]
    lines = [f"{head}: {verdict.broken_total} of {verdict.orderings} orderings break it.", ""]
    lines += [f"One of the {count} that end with: {run.problem}", "", trace_text(run), ""]
    for name in dict.fromkeys(step.who for step in run.trace):
        reply = run.reply(name)
        if reply is not None:
            lines.append(f"  {name} is told: {reply}")
    lines.append(f"  State: {_state(run.world)}")
    return "\n".join(lines)


# --- saves --------------------------------------------------------------------


def saves_text() -> str:
    lines = ["The world lives in memory and each character is saved on its own: the trade, two saves, a stop anywhere."]
    variants = [
        ("each character saved on its own", saves.after_restart_separate_saves),
        ("the trade in one transaction", saves.after_restart_trade_in_one_transaction),
    ]
    for title, after_restart in variants:
        counted = saves.outcomes(after_restart)
        lines.append(f"  {title} ({sum(counted.values())} histories):")
        for outcome, count in counted.items():
            mark = "  " if outcome in saves.CONSISTENT else "!!"
            lines.append(f"    {mark} {count:>2}  {outcome}")
    return "\n".join(lines)


def saves_detail() -> str:
    lines = [saves_text(), "", "The histories that break the rules when each character is saved on its own:"]
    found = [(" -> ".join(history) + " -> the server stops", verdict) for history, verdict in
             saves.broken(saves.after_restart_separate_saves)]
    width = max(len(story) for story, _ in found)
    for story, verdict in sorted(found, key=lambda item: item[1]):
        lines.append(f"  {story:<{width}}   =>   {verdict}")
    return "\n".join(lines)


# --- numbers ------------------------------------------------------------------


def numbers_text(
    chance: float | None,
    per_second: float,
    window: float,
    spread: float,
    start: int,
    target: int,
    detect: float,
    round_minutes: float,
) -> str:
    race = numbers.race_chance(window, spread)
    measured = numbers.simulate_race(window, spread)
    rows = [(race, "the race above"), (0.001, "one in 1,000"), (0.0001, "one in 10,000")]
    if chance is not None:
        rows.insert(0, (chance, "your --chance"))
    lines = [
        "1. One attempt",
        f"   The read and the write are {window:g} ms apart. Two requests land within +/- {spread:g} ms of each other.",
        f"   chance = window / spread = {race:.2%}   (measured with 200,000 random tries: {measured:.2%})",
        "",
        '2. Many attempts: "it only works one time in a thousand"',
        f"   {'chance per attempt':<28}{'attempts for 99%':>18}{f'at {per_second:g} a second':>18}",
    ]
    for p, label in rows:
        tries = numbers.attempts_needed(p)
        lines.append(f"   {f'{p:.2%}  ({label})':<28}{tries:>18,}{_duration(tries / per_second):>18}")
    rounds = numbers.rounds_to_reach(start, target)
    growth = numbers.growth_before_detection(detect, round_minutes)
    half = numbers.growth_before_detection(detect / 2, round_minutes)
    lines += [
        "",
        "3. A dupe doubles",
        f"   from {start:,} to {target:,}: {rounds} rounds",
        f"   after 10 / 20 / 30 rounds: x{2**10:,} / x{2**20:,} / x{2**30:,}",
        "",
        "4. The time until somebody notices is in the exponent",
        f"   one round every {round_minutes:g} minutes:",
        f"   {f'noticed after {detect:g} minutes':<28}x{growth:,.0f}",
        f"   {f'noticed after {detect / 2:g} minutes':<28}x{half:,.0f}",
        "   An upper bound for a pure doubling loop. Inventory size and cooldowns slow real ones down.",
    ]
    return "\n".join(lines)


def _duration(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f} seconds"
    if seconds < 5400:
        return f"{seconds / 60:.1f} minutes"
    return f"{seconds / 3600:.1f} hours"
