"""python -m loot                      the six checks, every design
python -m loot show 01 naive        one broken ordering, statement by statement
python -m loot saves                state in memory, saved one character at a time
python -m loot generated            handlers written by language models, through the same checks
python -m loot numbers              the arithmetic, with your own values
"""

from __future__ import annotations

import argparse

from . import generated, report
from .checks import CHECKS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m loot", description="Executable models for the talk Bank-Grade Loot."
    )
    commands = parser.add_subparsers(dest="command")

    commands.add_parser("checks", help="run the six checks against every design (the default)")

    show = commands.add_parser("show", help="print one broken ordering, statement by statement")
    show.add_argument("check", choices=sorted(CHECKS), help="check number")
    show.add_argument("design", help="design name, as printed by the checks")
    show.add_argument("problem", nargs="?", default="", help="pick an ordering whose problem contains this text")

    commands.add_parser("saves", help="state in memory, saved one character at a time")

    written = commands.add_parser("generated", help="handlers written by language models, through the same checks")
    written.add_argument("name", nargs="?", help="a handler's name, to see one of its broken orderings")
    written.add_argument("check", nargs="?", default="01", help="the check to show: 01, 02, 03, 04 or 2x (default 01)")

    numbers = commands.add_parser("numbers", help="the arithmetic behind the talk, with your own values")
    numbers.add_argument("--chance", type=float, help="add your own chance per attempt, for example 0.005")
    numbers.add_argument("--per-second", type=float, default=10, help="attempts a script makes per second (default 10)")
    numbers.add_argument("--window", type=float, default=1, help="ms between the read and the write (default 1)")
    numbers.add_argument("--spread", type=float, default=50, help="how close two requests land, +/- ms (default 50)")
    numbers.add_argument("--start", type=int, default=1, help="what the duper starts with (default 1)")
    numbers.add_argument("--target", type=int, default=9_000_000_000, help="what the duper ends with (default 9e9)")
    numbers.add_argument("--detect", type=float, default=180, help="minutes until somebody notices (default 180)")
    numbers.add_argument("--round", type=float, default=5, dest="round_minutes", help="minutes per doubling (5)")

    args = parser.parse_args(argv)

    if args.command == "show":
        names = [design.name for design in report.designs_for(args.check)]
        if args.design not in names:
            parser.error(f"check {args.check} knows these designs: {', '.join(names)}")
        print(report.show(args.check, args.design, args.problem))
    elif args.command == "saves":
        print(report.saves_detail())
    elif args.command == "generated":
        print(generated.show(args.name, args.check) if args.name else generated.table())
    elif args.command == "numbers":
        print(
            report.numbers_text(
                args.chance,
                args.per_second,
                args.window,
                args.spread,
                args.start,
                args.target,
                args.detect,
                args.round_minutes,
            )
        )
    else:
        print(report.full_report())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
