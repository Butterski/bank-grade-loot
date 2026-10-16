"""The three skills for a coding agent must be valid skills, and say only what the program proves."""

import json
import pathlib
import re
import subprocess
import sys

import pytest

from loot import report, saves
from loot.checks import CHECKS

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"
NAMES = ["designing-trades", "finding-dupes", "planning-dupe-fixes"]
README = (ROOT / "README.md").read_text(encoding="utf-8")


def skill_text(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


def front_matter(text: str) -> tuple[str, str]:
    match = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", text)
    assert match, "SKILL.md must start with name and description between --- lines"
    return match.group(1), match.group(2)


def everything() -> str:
    """Every Markdown file of every skill, with the line breaks taken out, for phrase checks."""
    return " ".join(path.read_text(encoding="utf-8") for path in SKILLS.rglob("*.md")).replace("\n", " ")


def test_the_skills_folder_holds_exactly_the_three_skills():
    assert sorted(path.name for path in SKILLS.iterdir() if path.is_dir()) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_the_front_matter_follows_the_agent_skills_specification(name):
    skill_name, description = front_matter(skill_text(name))
    assert skill_name == name, "the name must match the directory"
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert 0 < len(description) <= 1024
    assert "<" not in description and "claude" not in name and "anthropic" not in name
    assert "Use when" in description, "the description says when to use the skill"
    assert not description.startswith(("I ", "You ")), "third person"


@pytest.mark.parametrize("name", NAMES)
def test_the_body_is_short_and_every_file_it_points_at_exists_one_level_deep(name):
    text = skill_text(name)
    body = text.split("---\n", 2)[2]
    assert body.count("\n") < 500
    links = re.findall(r"\]\(([^)]+)\)", text) + re.findall(r"(scripts/[\w./-]+\.py)", text)
    assert links, "a skill points at its reference material"
    for link in links:
        if link.startswith("http"):
            continue
        assert (SKILLS / name / link).is_file(), link
        assert len(pathlib.PurePosixPath(link).parts) == 2, f"one level deep: {link}"
        assert "\\" not in link


@pytest.mark.parametrize("name", NAMES)
def test_every_reference_file_longer_than_a_hundred_lines_starts_with_a_table_of_contents(name):
    for path in (SKILLS / name / "references").glob("*.md"):
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) > 100:
            assert "## Contents" in lines[:5], path


def test_the_six_checks_in_the_review_reference_are_the_ones_the_program_names():
    six = (SKILLS / "finding-dupes" / "references" / "six-checks.md").read_text(encoding="utf-8")
    headings = re.findall(r"^## (\d\d) (.+)$", six, re.M)
    assert headings == [(number, title) for number, (title, _) in CHECKS.items()]
    for section in re.split(r"^## \d\d ", six, flags=re.M)[1:]:
        for part in ("**Must hold:**", "**Failing code looks like:**", "**Holds with:**", "**How to check it:**"):
            assert part in section, section[:40]


def test_the_four_sentences_in_the_design_skill_are_the_ones_in_the_prompt():
    prompt = (ROOT / "generated" / "prompt-with-requirements.md").read_text(encoding="utf-8").splitlines()
    sentences = [line for line in prompt if line[:3] in ("1. ", "2. ", "3. ", "4. ")]
    assert len(sentences) == 4
    lines = skill_text("designing-trades").splitlines()
    for sentence in sentences:
        assert sentence in lines, sentence


def test_every_number_the_skills_quote_is_the_one_the_program_computes():
    flat = everything()
    counted = {
        ("01", "naive"): "duplicates the item in {broken} of {orderings} orderings",
        ("01", "naive-in-transaction"): "still {broken} of {orderings}",
        ("01", "optimistic-rows-ignored"): "{broken} of {orderings}",
        ("02", "naive"): "{broken} of {orderings} break",
        ("02", "naive-in-transaction"): "still {broken} of {orderings}",
        ("02", "result-stored-last"): "{broken} of {orderings}",
        ("02", "lock-and-check"): "{broken} of {orderings}",
        ("03", "naive"): "{broken} of {orderings} stop points",
        ("04", "naive"): "{broken} of {orderings}",
        ("04", "lock-and-check"): "{broken} of {orderings} stop points",
        ("05", "look-first"): "{broken} of {orderings} orderings",
        ("06", "save-then-call"): "{broken} of {orderings} stop points",
        ("06", "call-then-save"): "{broken} of {orderings}",
    }
    for (check, design), phrase in counted.items():
        verdict = report.run_check(check, design)
        assert phrase.format(broken=verdict.broken_total, orderings=verdict.orderings) in flat, (check, design)
    new_id = report.run_check("05", "once-per-event")
    assert f"breaks in {new_id.broken_total} of {new_id.orderings} for the same purchase under a new ID" in flat
    outbox = report.run_check("06", "outbox")
    assert f"grants 200 for one payment in {outbox.broken['200 gems for one payment']} of {outbox.orderings}" in flat
    shop = report.run_check("06", "reference")
    assert shop.holds and f"holds in all {shop.orderings:,} orderings" in flat
    histories = saves.outcomes(saves.after_restart_separate_saves)
    duplicate, gone, total = histories["duplicate: 2 x Sword 17"], histories["the sword is gone"], sum(histories.values())
    assert f"{total} histories of a trade, two saves and one stop" in flat
    assert f"{duplicate} end with two copies of the item, {gone} with none" in flat
    assert not saves.broken(saves.after_restart_trade_in_one_transaction)


def test_the_scanner_lists_the_trade_module_of_this_repository_first():
    script = SKILLS / "finding-dupes" / "scripts" / "find_value_paths.py"
    command = [sys.executable, str(script), str(ROOT / "loot"), "--top", "3", "--lines", "200"]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    first_row = next(line for line in result.stdout.splitlines() if line.startswith("| 1 |"))
    assert "`trade.py`" in first_row or "`shop.py`" in first_row, first_row
    assert "select_for_update" in result.stdout, "the lock calls of the trade are among the printed lines"
    missing = subprocess.run([sys.executable, str(script), str(ROOT / "no-such-dir")], capture_output=True, text=True)
    assert missing.returncode == 2 and "not a directory" in missing.stderr


@pytest.mark.parametrize("name", NAMES)
def test_each_skill_ships_a_test_prompt_with_its_files(name):
    evals = json.loads((SKILLS / name / "evals" / "evals.json").read_text(encoding="utf-8"))
    assert evals["skill_name"] == name
    for case in evals["evals"]:
        assert case["prompt"] and case["expectations"]
        for file in case["files"]:
            assert (SKILLS / name / file).is_file(), file


def test_the_readmes_point_at_the_three_skills():
    for name in NAMES:
        assert f"](skills/{name}/SKILL.md)" in README, name
    assert "cp -r skills/" in README
    generated = (ROOT / "generated" / "README.md").read_text(encoding="utf-8")
    assert "](../skills/designing-trades/SKILL.md)" in generated
