#!/usr/bin/env python3
"""Lists the files and lines of a code base that are likely to move value: the inventory for a dupe review.

It is a grep with a vocabulary, not a judge. Every file gets a score from the words of the domain (wallet, grant,
refund, inventory ...) and of storage (UPDATE, FOR UPDATE, commit, autocommit, save ...) it contains, and the
matching lines are printed so that nothing is reviewed from memory.

    python find_value_paths.py <root> [--top 25] [--lines 8] [--ext py,go,ts] [--include-tests]

Output is Markdown. Exit code 0 even when nothing is found; a code base with no hits is a finding too.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

# Each group is a reason to read the file. The weights say how strong the reason is: a wallet is a stronger hint
# than a save, because every game saves something.
GROUPS: dict[str, tuple[int, list[str]]] = {
    "value": (3, ["wallet", "balance", "coins?", "gold", "gems?", "currency", "credits?", "inventory", "item", "loot",
                  "reward", "grant", "claim", "purchase", "order", "trade", "transfer", "refund", "restore",
                  "rollback", "escrow", "auction", "market", "shop", "store", "receipt", "entitlement"]),
    "storage": (2, [r"\bUPDATE\b", r"\bINSERT\b", r"\bDELETE\b", r"\bSELECT\b", r"FOR\s+(UPDATE|SHARE)",
                    "select_for_update", "with_for_update", "lockForUpdate", "sharedLock", r"\bBEGIN\b",
                    r"\bCOMMIT\b", r"\bROLLBACK\b", r"\bUNIQUE\b", r"ON\s+CONFLICT", "transaction", "autocommit",
                    "savepoint", "session\\.(add|commit|flush|query)", r"\.save\(", r"\.update\(", r"\.create\(",
                    "upsert", "findOneAndUpdate", "bulkWrite", "\\$inc", "HINCRBY", "INCRBY", "MULTI", "WATCH"]),
    "repeat": (2, ["idempoten", "op_?id", "operation_?id", "request_?id", "dedup", "retry", "retries", "replay",
                   "webhook", "notification", "outbox", "reconcil", "publisher", "consumer", "ack"]),
    "memory": (1, ["autosave", "persist", "serialize", "flush", r"\bsave\b", "snapshot", "checkpoint", "logout",
                   "disconnect", "pending", "queue", "worker", "tick"]),
}
COMPILED = {name: (weight, re.compile("|".join(words), re.IGNORECASE)) for name, (weight, words) in GROUPS.items()}

SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "vendor", "third_party", "dist", "build", "target", "out",
             ".venv", "venv", "env", "__pycache__", ".tox", ".mypy_cache", ".pytest_cache", "coverage", ".idea",
             ".vscode", "Library", "Temp", "obj", "bin"}
TEXT_EXTENSIONS = {"py", "go", "ts", "tsx", "js", "mjs", "cjs", "java", "kt", "kts", "cs", "cpp", "cc", "cxx", "c",
                   "h", "hpp", "rs", "rb", "php", "lua", "gd", "scala", "swift", "sql", "prisma", "graphql", "proto",
                   "yaml", "yml", "toml", "ini", "cfg", "json", "env"}
TEST_MARKERS = re.compile(r"(^|[/_.\-])(test|tests|spec|specs|fixtures?|mocks?|__tests__)([/_.\-]|$)", re.IGNORECASE)


def files_under(root: pathlib.Path, extensions: set[str], include_tests: bool):
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if not path.is_file() or path.suffix.lstrip(".").lower() not in extensions:
            continue
        if not include_tests and TEST_MARKERS.search(str(path.relative_to(root)).replace("\\", "/")):
            continue
        yield path


def scan(path: pathlib.Path):
    """Score one file and collect its matching lines. Unreadable files are reported, not fatal."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        return None, f"could not read {path}: {error}"
    if "\0" in text[:4096]:
        return None, None  # binary, whatever the extension says
    score, hits, lines = 0, {name: 0 for name in COMPILED}, []
    for number, line in enumerate(text.splitlines(), start=1):
        groups = [name for name, (_, pattern) in COMPILED.items() if pattern.search(line)]
        if not groups:
            continue
        for name in groups:
            hits[name] += 1
            score += COMPILED[name][0]
        if len(groups) > 1 or "storage" in groups or "value" in groups:
            lines.append((number, ",".join(groups), line.strip()[:140]))
    lines.sort(key=lambda entry: -len(entry[1].split(",")))  # lines that hit several groups first, then file order
    return {"score": score, "hits": hits, "lines": lines}, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", help="directory to scan")
    parser.add_argument("--top", type=int, default=25, help="how many files to list (default 25)")
    parser.add_argument("--lines", type=int, default=8, help="matching lines to print per file (default 8)")
    parser.add_argument("--ext", default="", help="comma-separated extensions to scan (default: common code files)")
    parser.add_argument("--include-tests", action="store_true", help="also scan test and fixture files")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root)
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    extensions = {e.strip().lstrip(".").lower() for e in args.ext.split(",") if e.strip()} or TEXT_EXTENSIONS

    scored, notes, scanned = [], [], 0
    for path in files_under(root, extensions, args.include_tests):
        result, note = scan(path)
        scanned += 1
        if note:
            notes.append(note)
        if result and result["score"]:
            scored.append((path, result))
    scored.sort(key=lambda item: (-item[1]["score"], str(item[0])))

    print(f"# Value paths under `{root}`\n")
    print(f"{scanned} files scanned, {len(scored)} with hits. Score = value x3 + storage x2 + repeat x2 + memory x1,"
          " per matching line. Read the top of the list first; a high score is a reason to read, not a verdict.\n")
    if not scored:
        print("No file matched the vocabulary. Either value is handled elsewhere (a service, a platform SDK) or it is"
              " named differently here: pass --ext or grep for the game's own words for currency and items.")
        return 0
    print("| # | File | Score | value | storage | repeat | memory |\n|---|---|---|---|---|---|---|")
    for index, (path, result) in enumerate(scored[: args.top], start=1):
        hits = result["hits"]
        print(f"| {index} | `{path.relative_to(root).as_posix()}` | {result['score']} | {hits['value']} |"
              f" {hits['storage']} | {hits['repeat']} | {hits['memory']} |")
    for path, result in scored[: args.top]:
        print(f"\n## `{path.relative_to(root).as_posix()}`\n")
        for number, groups, line in result["lines"][: args.lines]:
            print(f"- {number}: `{line}`  ({groups})")
        rest = len(result["lines"]) - args.lines
        if rest > 0:
            print(f"- ... {rest} more matching lines")
    if notes:
        print("\n## Not scanned\n")
        for note in notes:
            print(f"- {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
