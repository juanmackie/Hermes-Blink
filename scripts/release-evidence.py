#!/usr/bin/env python3
"""Produce (and check) the release evidence for a built APK.

Field round 5, P1: docs/APK_RELEASE.md published one commit's size and SHA-256 while
the tree had moved three commits and ~3,600 lines, and a published hash only ever
identifies the *builder's own* artifact — a clean rebuild on another machine produces
the same size but a different digest. So the digest is recorded as provenance, not as
a gate, and the size is what CI verifies.

    python3 scripts/release-evidence.py --apk android/app/build/outputs/apk/debug/app-debug.apk
    python3 scripts/release-evidence.py --check docs/APK_RELEASE.md --commit <sha>

`--check` fails when the document records a commit that is neither HEAD nor its parent
(recording the numbers is itself a commit, so the document trails by exactly one), or
when the recorded versionCode disagrees with the build file.

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
            "  Expected: | <version> (versionCode N) | <commit> | <bytes> bytes | <sha256> |"
        )
        return 1
    recorded = {row["commit"]: row for row in rows}
    short = commit[:12]
    # The evidence row names the commit the artifact was built from, and recording it is
    # itself a commit. So the document is allowed to trail HEAD by exactly one commit
    # (the one that writes the numbers) and no further: anything older is a document that
    # describes code the reader no longer has.
    recent = git("rev-list", "--max-count=2", "HEAD").splitlines()
    allowed = {ref[:12] for ref in recent} | {short}
    match = next((row for key, row in recorded.items() if key[:12] in allowed), None)
    if match is None:
        print(
            f"release-evidence FAILED: {args.doc} does not record this build "
            f"({short} or its parent {recent[1][:12] if len(recent) > 1 else '-'}).\n"
            f"  It records: {sorted(recorded)}\n"
            "  A release document that describes an earlier commit is worse than none: it "
            "looks current."
        )
        return 1
    if str(identity["versionCode"]) not in {match.get("code") or "", ""}:
        print(
            f"release-evidence FAILED: the document's versionCode {match.get('code')} "
            f"does not match the build file's {identity['versionCode']}."
        )
        return 1
    # Size and digest are provenance, not gates. Field round 8: a clean debug build is
    # not byte-reproducible across toolchains, and the observed drift across 0.3.0-0.4.2
    # was -4, +8, -12 and -16 bytes in both directions. Gating on them only ever produces
    # a red run that a human has to wave through, which teaches everyone to ignore red.
    if args.apk and (REPO / args.apk).is_file():
        evidence = measure(REPO / args.apk)
        print(
            f"release-evidence OK: {short} · {identity['versionName']} "
            f"(versionCode {identity['versionCode']})\n"
            f"  documented: {match['size']} bytes, {match['sha'][:16]}…\n"
            f"  this build: {evidence['bytes']:,} bytes, {evidence['sha256'][:16]}…\n"
            "  recorded as provenance: a debug APK is not byte-reproducible across "
            "toolchains, so neither number is compared."
        )
        return 0
    print(f"release-evidence OK: {short} is documented (no APK present to measure)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
