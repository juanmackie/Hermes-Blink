#!/usr/bin/env python3
"""Refuse to ship a build that cannot be identified afterwards.

Field round 5, findings 1-3. Three separate defects, one root cause: nothing stopped a
binary from carrying a version number or a commit stamp that could not be trusted.

  1. **A reissued versionCode.** The device holds 0.4.6 / code 10 stamped
     `635b0e824c7e-dirty` — built from a tree where the bump to 10 was applied but
     uncommitted. `635b0e8` itself declares code 9. So code 10 is spent by a binary that
     is not the committed main, and building from main would have produced a *second*
     binary also claiming 10. Android installs it happily; `app_build_code` cannot tell
     them apart. This is the "four APKs sharing one version code" defect returning one
     version later.

  2. **Every build ever installed stamped `-dirty`** (5 of 5, measured from
     `publication_render_builds`). The provenance chain — `BuildConfig.COMMIT_SHA` →
     `X-Hermes-App-Sha` → `appBuildSha` — is fully implemented and has never once produced
     a trustworthy value on that device. Present and wrong is worse than absent: a query
     for "which build rendered revision N" returns a confident wrong answer.

  3. **A recorded digest nothing can check.** A debug APK is not byte-reproducible across
     toolchains, so the size and digest are provenance, not gates. That is sound. What was
     missing is any statement of *how* the artifact was produced and on what host.

This script is the enforcement, and it is deliberately pure Python: the review environment
has no JDK or Android SDK, so a check that needs one cannot be verified there.

    python3 scripts/check-build-provenance.py            # check the current tree and build
    python3 scripts/check-build-provenance.py --allow-dirty   # skip the clean-stamp rule
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[1]
BUILD_FILE = "android/app/build.gradle.kts"
DOC = "docs/APK_RELEASE.md"
BUILDCONFIG_GLOB = "android/app/build/generated/source/buildConfig/*/com/you/hermeswidget/BuildConfig.java"

VERSION_CODE = re.compile(r"^\s*versionCode\s*=\s*(\d+)", re.M)
VERSION_NAME = re.compile(r'^\s*versionName\s*=\s*"([^"]+)"', re.M)
COMMIT_FIELD = re.compile(r'COMMIT_SHA\s*=\s*"([^"]*)"')
# The evidence table: | `0.4.6` (versionCode 10) | `<commit>` | … |
EVIDENCE_ROW = re.compile(
    r"^\|\s*`[^`]+`\s*\(versionCode\s*(\d+)\)\s*\|\s*`([0-9a-f]{7,40})`",
    re.M,
)


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def tree_is_dirty() -> bool:
    return bool(git("status", "--porcelain", "--untracked-files=no"))


def build_identity() -> tuple[int | None, str | None]:
    text = (REPO / BUILD_FILE).read_text(encoding="utf-8")
    code = VERSION_CODE.search(text)
    name = VERSION_NAME.search(text)
    return (
        int(code.group(1)) if code else None,
        name.group(1) if name else None,
    )


def spent_version_codes() -> dict[int, str]:
    """versionCode -> the commit that published it, from the release document.

    The document is the ledger, which is why it records one row per release rather than
    only the newest: it is the thing that makes "do not reissue 10" checkable.
    """
    path = REPO / DOC
    if not path.is_file():
        return {}
    spent: dict[int, str] = {}
    for code, commit in EVIDENCE_ROW.findall(path.read_text(encoding="utf-8")):
        spent.setdefault(int(code), commit)
    return spent


def recent_commits(count: int = 2) -> list[str]:
    """HEAD and the commits just behind it.

    The release document trails HEAD by exactly one commit, because recording the numbers is
    itself a commit. An artifact built at HEAD~1 and recorded from it is the normal steady
    state, not a stale build: a gate that reads that as "older than the tree" makes
    following its own convention impossible.
    """
    return [line for line in git("rev-list", f"--max-count={count}", "HEAD").splitlines() if line]


def matches_any(sha: str, candidates: list[str]) -> bool:
    """True when `sha` is a prefix-compatible identifier of any candidate."""
    if not sha:
        return False
    return any(sha.startswith(c) or c.startswith(sha) for c in candidates if c)


def stamped_commit() -> str | None:
    for path in REPO.glob(BUILDCONFIG_GLOB):
        match = COMMIT_FIELD.search(path.read_text(encoding="utf-8"))
        if match:
            return match.group(1)
    return None


def main() -> int:
    global REPO

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-dirty", action="store_true",
        help="skip the clean-stamp rule (development builds; never for one that ships)",
    )
    parser.add_argument("--repo", default=None, help="repository to inspect (tests)")
    args = parser.parse_args()
    if args.repo:
        REPO = pathlib.Path(args.repo).resolve()

    problems: list[str] = []
    head = git("rev-parse", "HEAD")
    short_head = head[:12]
    code, name = build_identity()
    spent = spent_version_codes()

    # 1. A versionCode that has already been published cannot be reissued.
    if code is not None and code in spent:
        published_by = spent[code]
        same_build = matches_any(published_by, recent_commits(2))
        if not same_build:
            problems.append(
                f"versionCode {code} was already published by commit {published_by[:12]}. "
                f"Android will install a different binary over the one on the device and "
                f"app_build_code cannot tell them apart. Retire it: use {code + 1}."
            )
    if code is None:
        problems.append(f"no versionCode found in {BUILD_FILE}")

    # 2. The commit stamp has to be trustworthy.
    stamp = stamped_commit()
    if stamp is None:
        if not args.allow_dirty:
            problems.append(
                "no generated BuildConfig.java with COMMIT_SHA was found, so this build "
                "carries no commit identity at all. Build the app before checking, or pass "
                "--allow-dirty while developing."
            )
    elif stamp == "unknown" and not args.allow_dirty:
        problems.append(
            "COMMIT_SHA is 'unknown': the build could not read git, so this artifact "
            "cannot be attributed to any commit."
        )
    elif stamp.endswith("-dirty"):
        problems.append(
            f"COMMIT_SHA is '{stamp}': this artifact was built from a tree that did not "
            f"match a commit, so the version it reports is not a version anyone can "
            f"retrieve. Five consecutive builds on the review device were stamped this "
            f"way. Commit first, then build, or the stamp only records that nobody was "
            f"paying attention."
        )
    elif head and not matches_any(stamp, recent_commits(2)):
        problems.append(
            f"COMMIT_SHA is '{stamp}' but the tree is at {short_head}: this APK is more than "
            f"one commit behind, so the document would describe different code. Build again."
        )
    elif code is not None and code in spent and stamp and not matches_any(stamp, [spent[code]]):
        # The ledger and the artifact must agree. Two binaries claiming one versionCode
        # differ exactly here: the ledger names the first, this stamp is the second.
        problems.append(
            f"versionCode {code} is recorded against {spent[code][:12]} but this artifact "
            f"was built from {stamp}: two binaries would claim one versionCode, which is "
            f"the exact failure this gate exists to prevent. Retire the code."
        )

    # 3. The release this build belongs to must be documented.
    if code is not None and code not in spent and problems == []:
        problems.append(
            f"versionCode {code} ({name}) has no row in {DOC}. Record it with "
            f"scripts/release-evidence.py so the artifact and the code that made it stay "
            f"together."
        )

    if problems:
        print("check-build-provenance FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    where = "a clean stamp" if stamp and not stamp.endswith("-dirty") else "no stamp yet"
    print(
        f"check-build-provenance OK: {name} (versionCode {code}) · {short_head} · {where}"
    )
    if not args.allow_dirty and tree_is_dirty():
        # Not a failure on its own, but it is the condition that produced every -dirty
        # stamp to date, so it is worth saying out loud.
        print("  note: the working tree is dirty; commit before building anything that ships")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
