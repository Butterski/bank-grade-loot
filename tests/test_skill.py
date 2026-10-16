"""The skill for a coding agent must say what the program proves, and nothing else."""

import pathlib
import re

from loot import report, saves
from loot.checks import CHECKS

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILL = (ROOT / "skills" / "dupe-check" / "SKILL.md").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_the_skill_has_the_front_matter_an_agent_reads():
    match = re.match(r"---\nname: (\S+)\ndescription: (.+)\n---\n", SKILL)
    assert match, "SKILL.md must start with name and description between --- lines"
    assert match.group(1) == "dupe-check" == (ROOT / "skills" / "dupe-check").name
    assert len(match.group(2)) <= 1024


def test_the_skill_lists_the_six_checks_as_the_program_names_them():
    headings = re.findall(r"^### (\d\d) (.+)$", SKILL, re.M)
    assert headings == [(number, title) for number, (title, _) in CHECKS.items()]


def test_every_check_in_the_skill_says_how_to_check_it():
    sections = re.split(r"^### ", SKILL, flags=re.M)[1:]
    assert len(sections) == len(CHECKS)
    for section in sections:
        assert "- Breaks:" in section and "- Holds:" in section and "- How to check it:" in section, section[:40]


def test_the_four_sentences_in_the_skill_are_the_ones_in_the_prompt():
    prompt = (ROOT / "generated" / "prompt-with-requirements.md").read_text(encoding="utf-8").splitlines()
    sentences = [line for line in prompt if line[:3] in ("1. ", "2. ", "3. ", "4. ")]
    assert len(sentences) == 4
    for sentence in sentences:
        assert sentence in SKILL.splitlines(), sentence


def test_the_numbers_in_the_skill_are_the_ones_the_program_computes():
    naive = report.run_check("01", "naive")
    in_transaction = report.run_check("01", "naive-in-transaction")
    sent_twice = report.run_check("02", "result-stored-last")
    flat = SKILL.replace("\n", " ")
    for phrase in (
        f"duplicates the sword in {naive.broken_total} of {naive.orderings} orderings",
        f"still {in_transaction.broken_total} of {in_transaction.orderings}",
        f"{sent_twice.broken_total} of {sent_twice.orderings} orderings give one copy the wrong answer",
    ):
        assert phrase in flat, phrase
    counted = saves.outcomes(saves.after_restart_separate_saves)
    duplicate, gone, total = counted["duplicate: 2 x Sword 17"], counted["the sword is gone"], sum(counted.values())
    assert f"{total} histories of a trade, two saves and one stop: {duplicate} end with two swords, {gone} with none" in flat


def test_the_readmes_point_at_the_skill():
    assert "](skills/dupe-check/SKILL.md)" in README
    assert "cp -r skills/dupe-check" in README
    generated = (ROOT / "generated" / "README.md").read_text(encoding="utf-8")
    assert "](../skills/dupe-check/SKILL.md)" in generated
