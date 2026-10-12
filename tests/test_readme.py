"""The README must not drift away from the program.

Every output line quoted there has to be printed today, the code excerpt has
to be the real code, the tables and the picture have to be computed, and the
exercise has to end the way the page says it does.
"""

import importlib.util
import pathlib

from loot import report, saves, trade
from loot.__main__ import main
from loot.checks import check_01_two_buyers, double_spend
from loot.trade import TradeDesign, rows_world

ROOT = pathlib.Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")


def fenced_blocks(language: str) -> list[tuple[str, list[str]]]:
    """Every fenced block in that language, with the line right above it (a marker comment, if any)."""
    blocks, current, above, last_text = [], None, "", ""
    for line in README.splitlines():
        if line.startswith("```"):
            if current is None and line[3:].strip() == language:
                current, above = [], last_text
            elif current is not None:
                blocks.append((above, current))
                current = None
        elif current is not None:
            current.append(line)
        if line.strip():
            last_text = line.strip()
    return blocks


def test_every_quoted_output_line_is_printed_by_the_program(capsys):
    for command in ([], ["show", "01", "naive"], ["saves"], ["numbers"]):
        main(command)
    printed = {line.rstrip() for line in capsys.readouterr().out.splitlines()}
    quoted = [line for _, block in fenced_blocks("text") for line in block if line.strip() and not line.startswith("$")]
    assert len(quoted) > 40
    for line in quoted:
        assert line.rstrip() in printed, f"the README quotes a line the program does not print: {line!r}"


def test_the_code_excerpt_is_the_real_code():
    source = (ROOT / "loot" / "trade.py").read_text(encoding="utf-8")
    (excerpt,) = [block for marker, block in fenced_blocks("python") if marker == "<!-- excerpt -->"]
    assert "\n".join(excerpt) in source


def test_the_table_of_designs_is_the_one_the_program_computes():
    assert report.markdown_matrix() in README


def test_the_headline_numbers_are_real():
    naive = report.run_check("01", "naive")
    in_transaction = report.run_check("01", "naive-in-transaction")
    sent_twice = report.run_check("02", "result-stored-last")
    shop = report.run_check("06", "reference")
    assert shop.holds
    for phrase in (
        f"**{naive.broken_total} of {naive.orderings}** orderings end with two swords",
        f"still **{in_transaction.broken_total} of {in_transaction.orderings}**",
        f"**{sent_twice.broken_total} of {sent_twice.orderings}** orderings give one copy the wrong answer",
        f"**0 of {shop.orderings:,}**",
        f"holds in all {shop.orderings:,} orderings",
    ):
        assert phrase in README, phrase
    assert report.run_check("01", "lock-and-check").orderings == 2
    assert "200 gems for one payment" in report.run_check("06", "outbox").broken
    counted = saves.outcomes(saves.after_restart_separate_saves)
    assert (counted["duplicate: 2 x Sword 17"], counted["the sword is gone"], sum(counted.values())) == (2, 2, 16)


def test_the_exercise_ends_the_way_the_readme_says():
    (code,) = [block for marker, block in fenced_blocks("python") if marker == "<!-- exercise -->"]
    namespace = dict(vars(trade))
    exec("\n".join(code), namespace)
    handler = namespace["buy_item_lock_only"]
    design = TradeDesign("item-lock-only", "lock the item, look at the wallet", rows_world, handler)
    assert check_01_two_buyers(design).holds
    verdict = double_spend(design)
    assert set(verdict.broken) == {"negative balance"}
    claim = f"{verdict.broken_total} of {verdict.orderings} orderings with a negative balance"
    assert claim in README.replace("\n", " ")


def test_the_picture_is_drawn_from_todays_output():
    spec = importlib.util.spec_from_file_location("make_demo", ROOT / "docs" / "make_demo.py")
    make_demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(make_demo)
    assert make_demo.render(make_demo.demo_lines()) == make_demo.TARGET.read_text(encoding="utf-8")
