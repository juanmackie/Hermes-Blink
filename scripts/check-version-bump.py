#!/usr/bin/env python3
"""Fail when a source change lands without a versionCode bump.

Field round 5, P1: four genuinely different APKs shipped as versionCode 2, so
`device_client_info.app_build_code = 2` could not answer "which build is on this
phone?" — the question build reporting was added to answer. A version number that
does not change is worse than no version number, because it looks authoritative.

The rule is narrow on purpose: only a change to the shipped app (its sources, its
resources, its manifest, its build file) demands a new versionCode. Docs, tests and
CI changes do not, or every README typo would need a release.

Exit 0 when the check passes, 1 with the reason when it does not.

    python3 scripts/check-version-bump.py [--base <ref>]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
BUILD_FILE = pathlib.Path("android/app/build.gradle.kts")

# Paths whose change means "the APK on the phone is different".
SHIPPED_PREFIXES = (
    "android/app/src/main/",
    "android/app/build.gradle.kts",
    "android/app/proguard-rules.pro",
    "android/build.gradle.kts",
    "android/gradle.properties",
)

VERSION_CODE = re.compile(r"^\s*versionCode\s*=\s*(\d+)", re.M)


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True, check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def commit_of_head() -> str:
    return git("rev-parse", "HEAD")


def version_code(text: str) -> int | None:
    match = VERSION_CODE.search(text)
    return int(match.group(1)) if match else None


def shipped_files(base: str) -> list[str]:
    # Committed changes plus anything still in the working tree, so the check is useful
    # before a commit as well as in CI.
    names = git("diff", "--name-only", f"{base}..HEAD").splitlines()
    names += git("diff", "--name-only", "HEAD").splitlines()
    return sorted({name for name in names if name.startswith(SHIPPED_PREFIXES)})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base", default=None,
        help="git ref to compare against (default: the commit before HEAD's merge-base with origin/main)",
    )
    args = parser.parse_args()

    base = args.base
    if base is None:
        # `merge-base origin/main HEAD` in a depth-1 clone resolves to HEAD itself, which
        # makes `git diff base..HEAD` empty and this gate vacuously green — it could never
        # fail, while reporting success. Field round 8: that is worse than a red run, so
        # refuse to evaluate rather than pretend.
        if not git("rev-parse", "--verify", "--quiet", "HEAD~1"):
            print(
                "check-version-bump FAILED (harness, not evidence): shallow clone.\n"
                "  This checkout has one commit and no parent, so there is no base to\n"
                "  diff against and this gate cannot run at all.\n"
                "  Fix the checkout, not the build:\n"
                "    - uses: actions/checkout@v4\n"
                "      with:\n"
                "        fetch-depth: 0\n"
                "  A gate that cannot evaluate must be red, not green."
            )
            return 1
        # Two shapes, one rule. On a push to main, origin/main *is* HEAD, so merging gives
        # an empty diff and the gate reports success without checking anything — the same
        # vacuous pass as the shallow clone, in a full history. On a push the meaningful
        # base is the commit that was on main before this one; on a pull request it is the
        # merge base with main.
        if git("rev-parse", "origin/main") == commit_of_head():
            base = "HEAD~1"
        else:
            base = git("merge-base", "origin/main", "HEAD") or "HEAD~1"
    if not base or base == commit_of_head():
        print(
            "check-version-bump FAILED: no usable base commit to compare against.\n"
            "  A shallow clone has no parent, and on a push to main `merge-base "
            "origin/main HEAD`\n  is HEAD, so the diff would be empty and this gate would "
            "pass without\n  checking anything. Use fetch-depth: 0, or pass --base <ref>."
        )
        return 1

    head_text = (REPO / BUILD_FILE).read_text(encoding="utf-8")
    if base == commit_of_head():
        print("check-version-bump FAILED: the base commit is HEAD, so the comparison is empty.")
        return 1
    base_text = git("show", f"{base}:{BUILD_FILE}")
    head_code = version_code(head_text)
    if head_code is None:
        print(f"check-version-bump FAILED: no versionCode in {BUILD_FILE}")
        return 1
    if not base_text:
        print(f"check-version-bump: {base} has no {BUILD_FILE} (first release); ok")
        return 0

    base_code = version_code(base_text)
    if base_code is None:
        print(f"check-version-bump FAILED: {base} has no versionCode to compare against")
        return 1

    touched = shipped_files(base)
    if not touched:
        print(
            f"check-version-bump OK: no shipped-app change since {base[:12]}"
        )
        return 0
    if head_code != base_code:
        print(
            f"check-version-bump OK: versionCode {base_code} -> {head_code} "
            f"({len(touched)} shipped file(s) changed)"
        )
        return 0

    sample = ", ".join(sorted(touched)[:5])
    print(
        f"check-version-bump FAILED: {len(touched)} shipped file(s) changed since "
        f"{base[:12]} but versionCode is still {head_code}.\n"
        f"  changed: {sample}\n"
        f"  Four different APKs sharing one version code is how a support question "
        f"('which build is this?') became unanswerable. Bump versionCode in "
        f"{BUILD_FILE}, or pass --base <ref> when this bump is expected."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
