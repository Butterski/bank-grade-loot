"""Draws docs/demo.svg from the program's real output.

    python docs/make_demo.py

The picture in the README is therefore never typed by hand. A test compares
the file with what this script would draw today.
"""

from __future__ import annotations

import pathlib
import re
import sys
from html import escape

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from loot import report  # noqa: E402
from loot.trade import TRADE_DESIGNS  # noqa: E402

TARGET = ROOT / "docs" / "demo.svg"

CHAR, LINE, LEFT, TOP, BOTTOM = 8.4, 21, 24, 58, 20
FONT = "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace"
TEXT, DIM, BRIGHT, RED, GREEN = "#c9d1d9", "#8b949e", "#f0f6fc", "#ff7b72", "#3fb950"
WORDS = re.compile(r"\b(FAILS|holds|ok)\b")


def demo_lines() -> list[str]:
    """The first check and the summary of the trade, exactly as `python -m loot` prints them."""
    checks = list(report.TRADE_CHECKS)
    verdicts = {check: [report.run_check(check, design.name) for design in TRADE_DESIGNS] for check in checks}
    lines = ["$ python -m loot", ""]
    lines += report.check_table("01", verdicts["01"]).splitlines()
    lines += ["", "  [...]", ""]
    lines += report.summary_table(verdicts, checks, TRADE_DESIGNS).splitlines()
    return lines


def _paint(line: str) -> str:
    if line.startswith("$ "):
        return f'<tspan fill="{DIM}">$</tspan><tspan fill="{BRIGHT}" font-weight="700">{escape(line[1:])}</tspan>'
    if line.startswith("Check "):
        return f'<tspan fill="{BRIGHT}" font-weight="700">{escape(line)}</tspan>'
    if line.lstrip().startswith(("design", "Stan and Kenny", "[...]")):
        return f'<tspan fill="{DIM}">{escape(line)}</tspan>'
    parts = []
    for piece in WORDS.split(line):
        if piece in ("FAILS", "holds", "ok"):
            parts.append(f'<tspan fill="{RED if piece == "FAILS" else GREEN}" font-weight="700">{piece}</tspan>')
        elif piece:
            parts.append(escape(piece))
    return "".join(parts)


def render(lines: list[str]) -> str:
    width = round(max(len(line) for line in lines) * CHAR + 2 * LEFT)
    height = TOP + len(lines) * LINE + BOTTOM
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}"'
        ' role="img" aria-label="Terminal output of python -m loot: the naive trade duplicates the sword in 910 of'
        ' 914 orderings, the reference design in none">',
        f'<rect width="{width}" height="{height}" rx="12" fill="#0d1117"/>',
        f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="none" stroke="#30363d"/>',
        '<circle cx="22" cy="21" r="6" fill="#ff5f57"/><circle cx="42" cy="21" r="6" fill="#febc2e"/>'
        '<circle cx="62" cy="21" r="6" fill="#28c840"/>',
        f'<g font-family="{FONT}" font-size="14" fill="{TEXT}">',
    ]
    for number, line in enumerate(lines):
        if not line:
            continue
        y = TOP + number * LINE
        length = f"{len(line) * CHAR:.1f}"
        out.append(
            f'<text x="{LEFT}" y="{y}" xml:space="preserve" style="white-space:pre" textLength="{length}"'
            f' lengthAdjust="spacing">{_paint(line)}</text>'
        )
    out += ["</g>", "</svg>"]
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    TARGET.write_text(render(demo_lines()), encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(ROOT)}")
