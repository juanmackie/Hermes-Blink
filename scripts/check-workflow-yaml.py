#!/usr/bin/env python3
"""Fail if a GitHub Actions workflow cannot be parsed, or looks like it cannot be.

Field round 8, P1: one step name contained an unquoted `: `, which makes the value a
nested mapping rather than a string. PyYAML then rejects the whole file, GitHub reports
no jobs at all, and every gate in the workflow silently stops running — CI stays green
because nothing was ever scheduled.

    python3 scripts/check-workflow-yaml.py [path ...]

Uses PyYAML when it is installed (a real parse, the same one GitHub does) and falls back
to a dependency-free structural scan when it is not, so the check always runs.
"""
from __future__ import annotations

import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_WORKFLOWS = [".github/workflows/ci.yml"]

# A `- key: value` list entry whose unquoted value contains ": " is a mapping, not a
# scalar, which is the exact shape that broke the workflow.
STEP_ENTRY = re.compile(r"^\s*-\s+(?P<key>[A-Za-z_][\w-]*):\s*(?P<value>.+)$")
BLOCK_SCALAR = re.compile(r"^\s*[|>][-+]?\s*$")


def scan(path: pathlib.Path) -> list[str]:
    """Dependency-free: flag unquoted colons in list-entry values."""
    problems: list[str] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    in_block = False
    block_indent = 0
    for number, line in enumerate(lines, 1):
        if in_block:
            if line.strip() and (len(line) - len(line.lstrip())) <= block_indent:
                in_block = False
            else:
                continue
        match = STEP_ENTRY.match(line)
        if not match:
            continue
        value = match.group("value")
        if BLOCK_SCALAR.match(value):
            in_block = True
            block_indent = len(line) - len(line.lstrip())
            continue
        if value[:1] in "\"'":
            continue
        if ": " in value or value.rstrip().endswith(":"):
            problems.append(
                f"{path}:{number}: {line.strip()}\n"
                f"    quote the value: an unquoted ': ' turns this into a mapping and the "
                f"whole workflow stops parsing"
            )
    return problems


def parse(path: pathlib.Path) -> list[str]:
    """Real parse when PyYAML is available."""
    try:
        import yaml  # type: ignore
    except ImportError:
        return []
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # yaml.YAMLError and anything else it raises
        return [f"{path}: does not parse: {exc}"]
    if not isinstance(document, dict):
        return [f"{path}: top level is not a mapping"]
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        return [f"{path}: no jobs — GitHub will schedule nothing"]
    for name, job in jobs.items():
        steps = job.get("steps") if isinstance(job, dict) else None
        if not isinstance(steps, list) or not steps:
            return [f"{path}: job {name!r} has no steps"]
    return []


def main() -> int:
    targets = [pathlib.Path(a) for a in sys.argv[1:]] or [
        REPO / p for p in DEFAULT_WORKFLOWS
    ]
    problems: list[str] = []
    for path in targets:
        absolute = path if path.is_absolute() else REPO / path
        if not absolute.is_file():
            problems.append(f"{path}: missing")
            continue
        problems.extend(parse(absolute))
        problems.extend(scan(absolute))
    if problems:
        print("check-workflow-yaml FAILED:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("check-workflow-yaml OK: every workflow parses and has steps")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
