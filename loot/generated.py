"""Trade handlers written by language models, run through the same checks.

The handlers are in generated/samples, exactly as the models wrote them.
generated/README.md says how they were made and how to add your own.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass
from functools import lru_cache

from .checks import (
    Verdict,
    check_01_two_buyers,
    check_02_same_request_twice,
    check_03_stops_before_commit,
    check_04_reply_lost,
    double_spend,
)
from .db import Database, ModelError, UniqueViolation
from .explore import Handler
from .report import _state, trace_text
from .trade import ITEM, PRICE, SELLER, START, Reply, TradeDesign

FOLDER = pathlib.Path(__file__).resolve().parent.parent / "generated"

COLUMNS = {
    "01": ("Two buyers", check_01_two_buyers),
    "02": ("Sent twice", check_02_same_request_twice),
    "03": ("Stops early", check_03_stops_before_commit),
    "04": ("Reply lost", check_04_reply_lost),
    "2x": ("One wallet, two purchases", double_spend),
}


@dataclass(frozen=True)
class Sample:
    file: str
    model: str
    prompt: str  # "plain" or "with requirements"
    tables: tuple = ()  # tables the handler asked for in a comment: ((name, key columns), ...)

    @property
    def name(self) -> str:
        return self.file.removesuffix(".py")

    @property
    def source(self) -> str:
        return (FOLDER / "samples" / self.file).read_text(encoding="utf-8")


def samples() -> list[Sample]:
    index = json.loads((FOLDER / "samples.json").read_text(encoding="utf-8"))
    return [
        Sample(
            entry["file"],
            entry["model"],
            entry["prompt"],
            tuple((name, tuple(key)) for name, key in entry.get("tables", {}).items()),
        )
        for entry in index
    ]


def world(extra_tables: tuple = ()) -> Database:
    """The tables the prompts describe: items, wallets and an empty operations table.

    A handler that says it needs one more table gets it, empty.
    """
    tables = {"items": ("id",), "wallets": ("player",), "operations": ("caller", "op_id"), **dict(extra_tables)}
    db = Database("game", **tables)
    db.load("items", {ITEM: {"owner": SELLER, "version": 7}})
    db.load("wallets", {player: {"coins": coins} for player, coins in START.items()})
    return db


def design(sample: Sample) -> TradeDesign:
    """Load the model's `buy` untouched and give it the two names the prompt promised."""
    namespace = {"Reply": Reply, "UniqueViolation": UniqueViolation}
    exec(compile(sample.source, sample.file, "exec"), namespace)
    written = namespace["buy"]

    def buy(db, op_id, buyer, seller=SELLER, item=ITEM, price=PRICE) -> Handler:
        reply = yield from written(db, op_id, buyer, seller, item, price)
        if not isinstance(reply, Reply):
            raise ModelError(f"buy returned {reply!r}, not a Reply")
        return reply

    return TradeDesign(sample.name, f"{sample.model}, prompt: {sample.prompt}", lambda: world(sample.tables), buy)


@lru_cache(maxsize=None)
def judge(sample: Sample, column: str) -> Verdict | str:
    """The verdict of one check, or a short text when the handler cannot be run at all."""
    try:
        return COLUMNS[column][1](design(sample))
    except ModelError as error:
        return f"cannot be checked: {error}"
    except Exception as error:  # the handler itself raised: a wrong call, a missing row
        return f"raises {type(error).__name__}: {error}"


def passes(sample: Sample, column: str) -> bool:
    verdict = judge(sample, column)
    return isinstance(verdict, Verdict) and verdict.holds


def _cell(sample: Sample, column: str) -> str:
    verdict = judge(sample, column)
    if isinstance(verdict, str):
        return "ERROR"
    return "ok" if verdict.holds else "FAILS"


def _details(sample: Sample, column: str) -> str:
    verdict = judge(sample, column)
    if isinstance(verdict, str):
        return verdict
    reasons = "; ".join(f"{problem} ({count})" for problem, count in verdict.broken.most_common())
    return f"{verdict.broken_total} of {verdict.orderings} orderings: {reasons}"


def table() -> str:
    lines = ["Handlers written by language models, unchanged (generated/README.md says how they were asked)", ""]
    counted = tally()
    for prompt in dict.fromkeys(sample.prompt for sample in samples()):
        clean, total = counted[prompt]
        lines.append(f"  prompt: {prompt} - {clean} of {total} pass every scenario")
        lines.append("  " + f"{'model':<24}" + "".join(f"{column:>7}" for column in COLUMNS))
        for sample in samples():
            if sample.prompt == prompt:
                lines.append("  " + f"{sample.model:<24}" + "".join(f"{_cell(sample, c):>7}" for c in COLUMNS))
        lines.append("")
    lines.append("  " + "   ".join(f"{column} = {title}" for column, (title, _) in COLUMNS.items()))
    failures = [(sample, c) for sample in samples() for c in COLUMNS if not passes(sample, c)]
    if failures:
        lines += ["", "What broke:"]
        for sample, column in failures:
            lines.append(f"  {sample.name:<28}{column}  {_details(sample, column)}")
    lines += ["", "One broken ordering, statement by statement:   python -m loot generated <name> <check>"]
    return "\n".join(lines)


def markdown_table() -> str:
    """The same results as the Markdown table in the README."""
    yes, no = "\u2705", "\u274c"
    head = "| Prompt | Model | " + " | ".join(f"{c}<br>{title}" for c, (title, _) in COLUMNS.items()) + " |"
    lines = [head, "|---|---|" + ":-:|" * len(COLUMNS)]
    for sample in samples():
        cells = " | ".join(yes if passes(sample, column) else no for column in COLUMNS)
        lines.append(f"| {sample.prompt} | {sample.model} | {cells} |")
    return "\n".join(lines)


def tally() -> dict[str, tuple[int, int]]:
    """For each prompt: how many handlers pass everything, out of how many."""
    counted: dict[str, tuple[int, int]] = {}
    for sample in samples():
        clean, total = counted.get(sample.prompt, (0, 0))
        counted[sample.prompt] = (clean + all(passes(sample, column) for column in COLUMNS), total + 1)
    return counted


def show(name: str, column: str) -> str:
    found =[sample for sample in samples() if sample.name == name]
    if not found:
        return "No such handler. The names: " + ", ".join(sample.name for sample in samples())
    if column not in COLUMNS:
        return "No such check. The checks: " + ", ".join(COLUMNS)
    sample = found[0]
    verdict = judge(sample, column)
    head = f'{sample.name} ({sample.model}, prompt: {sample.prompt}), check {column} ({COLUMNS[column][0]})'
    if isinstance(verdict, str):
        return f"{head}: {verdict}"
    if verdict.holds:
        return f"{head}: holds in all {verdict.orderings} orderings. Nothing to show."
    run = verdict.example()
    lines = [f"{head}: {verdict.broken_total} of {verdict.orderings} orderings break it.", ""]
    lines += [f"One of the {verdict.broken[run.problem]} that end with: {run.problem}", "", trace_text(run), ""]
    for who in dict.fromkeys(step.who for step in run.trace):
        if run.reply(who) is not None:
            lines.append(f"  {who} is told: {run.reply(who)}")
    lines.append(f"  State: {_state(run.world)}")
    return "\n".join(lines)
