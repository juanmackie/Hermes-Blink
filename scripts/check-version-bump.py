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


def bump_base() -> str | None:
    """The parent of the last commit that set the current versionCode.

    Everything shipped after that bump is code the bump does not describe, which is the
    whole point of the check. Falls back to HEAD~1 when the bump is not in this history.
    """
    build = "android/app/build.gradle.kts"
    if not git("rev-parse", "--verify", "--quiet", "HEAD~1"):
        return None
    line = (REPO / build).read_text(encoding="utf-8")
    match = re.search(r"^\s*versionCode\s*=\s*(\d+)", line, re.M)
    if not match:
        return None
    bump = git("log", "-1", "--format=%H", "-S", f"versionCode = {match.group(1)}", "--", build)
    if not bump:
        return git("rev-parse", "HEAD~1") or None
    parent = git("rev-parse", "--verify", "--quiet", f"{bump}~1") or git("rev-parse", "HEAD~1")
    return parent or None


def version_code(text: str) -> int | None:
    match = VERSION_CODE.search(text)
    return int(match.group(1)) if match else None


def shipped_paths(span: str) -> list[str]:
    """Shipped files changed in a commit span (a..b)."""
    return sorted(
        name for name in git("diff", "--name-only", span).splitlines()
        if name.startswith(SHIPPED_PREFIXES)
    )


def shipped_files(base: str) -> list[str]:
    # Committed changes plus anything still in the working tree, so the check is useful
    # before a commit as well as in CI.
    names = git("diff", "--name-only", f"{base}..HEAD").splitlines()
    names += git("diff", "--name-only", "HEAD").splitlines()
    return sorted({name for name in names if name.startswith(SHIPPED_PREFIXES)})


def uncommitted_shipped_paths() -> list[str]:
    """Shipped files changed but not yet committed."""
    names = git("diff", "--name-only", "HEAD").splitlines()
    names += git("diff", "--name-only", "--cached").splitlines()
    return sorted({name for name in names if name.startswith(SHIPPED_PREFIXES)})


def bump_commit(version: int) -> str:
    """The commit that set this versionCode, or "" when it is not in this history."""
    return git(
        "log", "-1", "--format=%H", "-S", f"versionCode = {version}", "--", BUILD_FILE
    )


def main() -> int:
    # A copied script still anchors on its own __file__, so a fixture test would silently
    # inspect the real tree and pass without checking anything. --repo makes the target
    # explicit.
    global REPO
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=None, help="repository to inspect (tests)")
    parser.add_argument(
        "--base", default=None,
        help="git ref to compare against (default: the commit before HEAD's merge-base with origin/main)",
    )
    args = parser.parse_args()
    if args.repo:
        REPO = pathlib.Path(args.repo).resolve()

    if args.base is not None:
        base = args.base
    else:
        base = bump_base()
    if base is None:
        # A base we could not determine is not a pass. The previous version printed
        # "could not determine a base commit; skipping" and returned 0, which is a gate
        # reporting success without having checked anything.
        print(
            "check-version-bump FAILED: no base commit to compare against.\n"
            "  A shallow or partial clone hides the commit that set the current "
            "versionCode.\n"
            "  Use fetch-depth: 0, or pass --base <ref>."
        )
        return 1
    resolved_base = git("rev-parse", "--verify", "--quiet", base) or base
    if resolved_base.startswith(commit_of_head()):
        print(
            "check-version-bump FAILED: the base commit is HEAD, so the window would be empty "
            "and this check would report success without checking anything.\n"
            "  Use fetch-depth: 0, or pass --base <ref>."
        )
        return 1
    base = resolved_base

    head_text = (REPO / BUILD_FILE).read_text(encoding="utf-8")
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

    # The question is not "did the number move" but "does the bump cover the tree": app
    # code that landed *after* the bump is code the bump does not describe, whether or not
    # a later bump exists. An earlier version compared the two version numbers, which let
    # shipped code sit after its bump unnoticed, and then a docs-only commit reset the
    # window entirely. Finding 4, both halves.
    bump = bump_commit(head_code) or ""
    if bump:
        after = shipped_paths(f"{bump}..HEAD")
        # The working tree counts too, so a developer sees this before committing rather
        # than only in CI. Committed history is what CI has; both are what the bump must
        # cover.
        after = sorted(set(after) | set(uncommitted_shipped_paths()))
        if after:
            bump_short = bump[:12]
            print(
                f"check-version-bump FAILED: {len(after)} shipped file(s) changed after the "
                f"versionCode {head_code} bump in {bump_short}.\n"
                f"  window: {bump_short}..HEAD\n"
                f"  changed: {', '.join(after[:5])}\n"
                f"  A version number that moves later does not cover code that shipped "
                f"before it. Bump versionCode again, or pass --base <ref>."
            )
            return 1
        span = git("rev-list", "--count", f"{bump}..HEAD") or "0"
        moved = "" if head_code == base_code else f" (moved {base_code} -> {head_code})"
        print(
            f"check-version-bump OK: the versionCode {head_code} bump in {bump[:12]}{moved} "
            f"still covers the tree: {span} commit(s) since it, none of them shipping app "
            f"code. This means the bump is current, not that nothing changed."
        )
        return 0

    # The bump is not in this history, so fall back to the requested window and be explicit.
    touched = shipped_files(base)
    if not touched:
        print(
            f"check-version-bump OK (fallback window {base[:12]}..HEAD): no shipped-app change "
            f"there. The versionCode {head_code} bump is not in this history, so this is a "
            f"weaker result than the normal one."
        )
        return 0
    sample = ", ".join(sorted(touched)[:5])
    print(
        f"check-version-bump FAILED: {len(touched)} shipped file(s) changed in "
        f"{base[:12]}..HEAD and the versionCode {head_code} bump is not in this history to "
        f"cover them.\n"
        f"  changed: {sample}\n"
        f"  Use fetch-depth: 0 so the bump can be located, or pass --base <ref>."
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
