"""Turns the saved replies in generated/raw into generated/samples and generated/samples.json.

    python generated/extract.py

A reply is saved as raw/<model>-<prompt>.md, exactly as the model sent it.
The sample is the first Python code block of that reply, character for
character. Nothing is fixed, renamed or reformatted on the way.
"""

from __future__ import annotations

import json
import pathlib
import re

FOLDER = pathlib.Path(__file__).resolve().parent
PROMPTS = {"plain": "plain", "with-requirements": "with requirements"}
BLOCK = re.compile(r"```python\n(.*?)\n```", re.S)
MODEL = re.compile(r"^MODEL:\s*(\S+)\s*$", re.M)


def main() -> None:
    extra = json.loads((FOLDER / "extra-tables.json").read_text(encoding="utf-8"))
    samples = FOLDER / "samples"
    samples.mkdir(exist_ok=True)
    for old in samples.glob("*.py"):
        old.unlink()
    index = []
    for reply in sorted((FOLDER / "raw").glob("*.md")):
        text = reply.read_text(encoding="utf-8")
        prompt = next(name for name in sorted(PROMPTS, key=len, reverse=True) if reply.stem.endswith("-" + name))
        family = reply.stem.removesuffix("-" + prompt)
        code = BLOCK.search(text).group(1) + "\n"
        model = MODEL.search(text)
        (samples / f"{reply.stem}.py").write_text(code, encoding="utf-8", newline="\n")
        index.append(
            {
                "file": f"{reply.stem}.py",
                "model": model.group(1) if model else family,
                "prompt": PROMPTS[prompt],
                "tables": extra.get(reply.stem, {}),
            }
        )
    index.sort(key=lambda entry: (entry["prompt"] != "plain", entry["model"]))
    (FOLDER / "samples.json").write_text(json.dumps(index, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{len(index)} samples")


if __name__ == "__main__":
    main()
