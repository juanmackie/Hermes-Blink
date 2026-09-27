#!/usr/bin/env python3
"""Produce (and check) the release evidence for a built APK.

Field round 5, P1: docs/APK_RELEASE.md published one commit's size and SHA-256 while
the tree had moved three commits and ~3,600 lines, and a published hash only ever
identifies the *builder's own* artifact — a clean rebuild on another machine produces
the same size but a different digest. So the digest is recorded as provenance, not as
a gate, and the size is what CI verifies.

    python3 scripts/release-evidence.py --apk android/app/build/outputs/apk/debug/app-debug.apk
    python3 scripts/release-evidence.py --check docs/APK_RELEASE.md --commit <sha>

`--check` asks two questions that cannot drift silently:

  1. **Is the recorded commit in this history at all?** It must be HEAD or an ancestor of
     it. A commit from another branch, a rewritten history, or a different repository is a
     failure — the document would be describing code the reader does not have.
  2. **Does the recorded versionCode match the build file?** That is the check that
     actually prevents "four APKs, one version number".

How far behind HEAD the document trails is *reported*, not failed on. Recording the
numbers is itself a commit, so one commit behind is the normal steady state; further behind
means nobody rebuilt, which the version-bump gate already catches on the shipped app. The
round-8 "HEAD or its parent" rule was narrower than the truth and failed on every honest
push, which is the same disease as a gate that never fails.

If the clone is too shallow to answer question 1, the check says so by name — "shallow
clone" — instead of reporting that the document is wrong.

Size and digest are *provenance*, never compared: a clean debug build is not
byte-reproducible across toolchains — observed drift was -4, +8, -12 and -16 bytes in both
directions — so a comparison there is a guaranteed red run that trains everyone to ignore
red. What is checked is the thing that cannot drift silently: which commit, which version.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_APK = pathlib.Path("android/app/build/outputs/apk/debug/app-debug.apk")
VERSION_CODE = re.compile(r"^\s*versionCode\s*=\s*(\d+)", re.M)
VERSION_NAME = re.compile(r"^\s*versionName\s*=\s*\"([^\"]+)\"", re.M)
BUILD_FILE = "android/app/build.gradle.kts"
# | `0.3.0` (versionCode 3) | `719cb8139b07` | 7,229,414 bytes | `<sha256>` |
TABLE_ROW = re.compile(
    r"^\|\s*`(?P<version>[0-9][0-9.]*)`\s*\(\s*versionCode\s*(?P<code>\d+)\s*\)\s*\|"
    r"\s*`(?P<commit>[0-9a-f]{7,40})`\s*\|\s*(?P<size>[\d,]+)\s*bytes?\s*\|"
    r"\s*`(?P<sha>[0-9a-f]{64})`\s*\|",
    re.M,
)


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def git_ok(*args: str) -> bool:
    """True when the git command succeeded, regardless of what it printed.

    `merge-base --is-ancestor` prints nothing in both cases and signals the answer through
    its exit status, so its result cannot be read from stdout.
    """
    result = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return result.returncode == 0


def build_identity() -> dict[str, object]:
    text = (REPO / BUILD_FILE).read_text(encoding="utf-8")
    code = VERSION_CODE.search(text)
    name = VERSION_NAME.search(text)
    return {
        "versionCode": int(code.group(1)) if code else None,
        "versionName": name.group(1) if name else None,
        "commit": git("rev-parse", "HEAD"),
    }


def measure(apk: pathlib.Path) -> dict[str, object]:
    if not apk.is_file():
        raise SystemExit(f"release-evidence: no such APK: {apk}")
    data = apk.read_bytes()
    identity = build_identity()
    return {
        **identity,
        "apk": str(apk.relative_to(REPO) if apk.is_absolute() else apk),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def rows_in_doc(doc: pathlib.Path) -> list[dict[str, str]]:
    if not doc.is_file():
        return []
    return [match.groupdict() for match in TABLE_ROW.finditer(doc.read_text(encoding="utf-8"))]


def resolve_row(rows: list[dict], commit: str, short: str) -> tuple[dict | None, int, str]:
    """The newest evidence row that is in this history, how far behind, and any problem.

    Returns (row, distance_in_commits, problem) where problem is "", "shallow" or
    "not-in-history". A row at HEAD has distance 0.
    """
    if not commit:
        return None, 0, "not-in-history"
    at_head = any(row["commit"].startswith(short) or short.startswith(row["commit"]) for row in rows)
    if at_head:
        row = next(r for r in rows if r["commit"].startswith(short) or short.startswith(r["commit"]))
        return row, 0, ""
    # No parent in this clone: we cannot place any older commit, and that is a harness
    # problem rather than a statement about the document.
    if not git("rev-parse", "--verify", "--quiet", "HEAD~1"):
        return None, 0, "shallow"
    # Ancestry, not resolvability. `git rev-parse <sha>` succeeds for any object the
    # repository still has, including one left behind by a rebase, and `rev-list --count
    # A..B` then reports a plausible distance for a commit that is in no branch's history.
    # That is how a rewritten history can keep looking current: this check passed on a
    # recorded commit that had been dropped from main.
    in_history = [
        (row, distance)
        for row in rows
        for full in [git("rev-parse", "--verify", "--quiet", row["commit"] + "^{commit}")]
        if full and git_ok("merge-base", "--is-ancestor", full, commit)
        for distance in [int(git("rev-list", "--count", f"{full}..{commit}") or "0")]
    ]
    if not in_history:
        return None, 0, "not-in-history"
    in_history.sort(key=lambda pair: pair[1])
    row, distance = in_history[0]
    return row, distance, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", default=str(DEFAULT_APK))
    parser.add_argument("--doc", default="docs/APK_RELEASE.md")
    parser.add_argument("--check", action="store_true", help="verify the document instead of printing")
    parser.add_argument("--commit", default=None, help="commit the document must record")
    args = parser.parse_args()

    doc = REPO / args.doc
    if not args.check:
        evidence = measure(pathlib.Path(args.apk) if pathlib.Path(args.apk).is_absolute()
                           else REPO / args.apk)
        print(json.dumps(evidence, indent=2))
        return 0

    # Check mode: the APK is optional; what matters is the document.
    commit = args.commit or git("rev-parse", "HEAD")
    identity = build_identity()
    rows = rows_in_doc(doc)
    if not doc.is_file():
        print(f"release-evidence FAILED: {args.doc} does not exist")
        return 1
    if not rows:
        print(
            f"release-evidence FAILED: {args.doc} has no release-evidence row.\n"
            "  Expected: | <version> (versionCode N) | <commit> | <bytes> bytes | <sha256> |\n"
            "  Generate one from a clean build with scripts/release-evidence.py."
        )
        return 1
    short = commit[:12]
    match, distance, problem = resolve_row(rows, commit, short)
    if problem == "shallow":
        print(
            f"release-evidence FAILED (harness, not evidence): shallow clone.\n"
            f"  This checkout has one commit (HEAD is {short}) and no parent, so no\n"
            f"  recorded commit can be placed in the history and the check cannot run.\n"
            "  Fix the checkout, not the document:\n"
            "    - uses: actions/checkout@v4\n"
            "      with:\n"
            "        fetch-depth: 0\n"
            f"  It records: {sorted(row['commit'][:12] for row in rows)}"
        )
        return 1
    if problem == "not-in-history":
        print(
            f"release-evidence FAILED: {args.doc} records a commit that is not in this "
            f"history.\n  HEAD: {short}\n  recorded: {sorted(row['commit'][:12] for row in rows)}\n"
            "  A release document that describes code this repository does not contain is "
            "worse than none: it looks current."
        )
        return 1
    if match is None:
        print(
            f"release-evidence FAILED: {args.doc} has no evidence row for this build "
            f"({short}).\n  It records: {sorted(row['commit'][:12] for row in rows)}\n"
            "  Generate it with scripts/release-evidence.py against a clean build."
        )
        return 1
    if str(identity["versionCode"]) not in {match.get("code") or "", ""}:
        print(
            f"release-evidence FAILED: the newest recorded row is versionCode "
            f"{match.get('code')} but the build file says {identity['versionCode']}.\n"
            f"  recorded commit: {match['commit'][:12]}"
            + (f" ({distance} commit(s) behind HEAD)" if distance else "")
            + "\n  Bump the version, rebuild, and record the new numbers."
        )
        return 1
    # Size and digest are provenance, not gates. Field round 8: a clean debug build is
    # not byte-reproducible across toolchains, and the observed drift across 0.3.0-0.4.2
    # was -4, +8, -12 and -16 bytes in both directions. Gating on them only ever produces
    # a red run that a human has to wave through, which teaches everyone to ignore red.
    behind = f" · {distance} commit(s) behind HEAD" if distance else " · at HEAD"
    print(
        f"release-evidence OK: {identity['versionName']} (versionCode "
        f"{identity['versionCode']}) records {match['commit'][:12]}{behind}"
    )
    if args.apk and (REPO / args.apk).is_file():
        evidence = measure(REPO / args.apk)
        print(
            f"  documented: {match['size']} bytes, {match['sha'][:16]}…\n"
            f"  this build: {evidence['bytes']:,} bytes, {evidence['sha256'][:16]}…\n"
            "  provenance only: a debug APK is not byte-reproducible across toolchains, so "
            "neither number is compared."
        )
    else:
        print("  no APK present, so size and digest were not measured")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
