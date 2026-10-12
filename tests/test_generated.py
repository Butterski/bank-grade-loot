"""The handlers written by models: the replies, the samples, the index and the README must agree."""

import json
import pathlib

from loot import generated
from loot.__main__ import main

ROOT = pathlib.Path(__file__).resolve().parent.parent
FOLDER = ROOT / "generated"
README = (ROOT / "README.md").read_text(encoding="utf-8")


def test_every_sample_is_the_code_block_of_its_reply_unchanged():
    for sample in generated.samples():
        reply = (FOLDER / "raw" / f"{sample.name}.md").read_text(encoding="utf-8")
        assert "```python\n" + sample.source + "```" in reply, sample.name


def test_the_index_lists_exactly_the_files_that_exist():
    index = json.loads((FOLDER / "samples.json").read_text(encoding="utf-8"))
    listed = sorted(entry["file"] for entry in index)
    assert listed == sorted(path.name for path in (FOLDER / "samples").glob("*.py"))
    assert listed == sorted(path.stem + ".py" for path in (FOLDER / "raw").glob("*.md"))


def test_the_second_prompt_is_the_first_one_plus_the_requirements():
    plain = (FOLDER / "prompt-plain.md").read_text(encoding="utf-8").splitlines()
    longer = (FOLDER / "prompt-with-requirements.md").read_text(encoding="utf-8").splitlines()
    rest = iter(longer)
    assert all(line in rest for line in plain), "the short prompt must be contained in the long one, in order"
    added = [line for line in longer if line and line not in plain]
    assert len(added) == 6  # one sentence, four requirements, one table


def test_the_table_in_the_readme_is_the_one_the_program_computes():
    assert generated.markdown_table() in README


def test_the_four_sentences_in_the_readme_are_the_ones_in_the_prompt():
    prompt = (FOLDER / "prompt-with-requirements.md").read_text(encoding="utf-8").splitlines()
    sentences = [line for line in prompt if line[:3] in ("1. ", "2. ", "3. ", "4. ")]
    assert len(sentences) == 4
    for sentence in sentences:
        assert f"> {sentence}" in README


def test_the_counts_in_both_readmes_are_the_ones_the_program_computes():
    counted = generated.tally()
    own = (FOLDER / "README.md").read_text(encoding="utf-8")
    for prompt, file in (("plain", "prompt-plain.md"), ("with requirements", "prompt-with-requirements.md")):
        clean, total = counted[prompt]
        row = next(line for line in own.splitlines() if line.startswith(f"| [`{file}`]"))
        assert row.endswith(f"| {clean} of {total} |"), row
    assert "worked for four models out of four" in README and counted["with requirements"] == (4, 4)
    assert "three of four still do" in README and counted["plain"] == (3, 4)


def test_what_the_readme_says_about_the_handlers_is_true():
    samples = generated.samples()
    assert len(samples) == 8
    assert generated.tally() == {"plain": (3, 4), "with requirements": (4, 4)}
    for sample in samples:
        for column in ("01", "03", "2x"):  # no duplicate, no half trade, no balance below zero
            assert generated.passes(sample, column), (sample.name, column)
    problems = set()
    for sample in samples:
        for column in generated.COLUMNS:
            verdict = generated.judge(sample, column)
            assert not isinstance(verdict, str), f"{sample.name} cannot be run: {verdict}"
            problems |= set(verdict.broken)
    assert problems == {"wrong answer to the repeated request"}


def test_a_handler_that_cannot_be_run_is_reported_not_raised():
    broken = generated.Sample("fable-plain.py", "nobody", "plain", (("items", ("wrong", "key")),))
    assert isinstance(generated.judge(broken, "01"), str)


def test_the_command_prints_the_table_and_one_ordering(capsys):
    assert main(["generated"]) == 0
    assert "prompt: with requirements - 4 of 4 pass every scenario" in capsys.readouterr().out
    assert main(["generated", "fable-plain", "02"]) == 0
    assert "you already own this item" in capsys.readouterr().out
