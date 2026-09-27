"""The release gates, exercised against real git repositories.

Field round 8: `check-version-bump.py` and `release-evidence.py` both read git history,
and both shipped without ever being run in the clone CI actually produces. The evidence
check failed on every honest push — depth 1 has no parent, so the "HEAD or its parent"
rule could not be evaluated — while the version-bump gate passed **vacuously**: with
`origin/main == HEAD` its diff was empty, so it reported success without checking anything.

A gate that cannot run must be red and say why. A gate that runs must fail for real drift.
Both properties are tested here, in temporary repositories, in both clone shapes.

The scripts resolve the repository from their own `__file__`, so these tests run the
*copies* inside the fixture — running the originals would quietly inspect this repository
instead, which is the mistake the first draft of this file made.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[3]
SCRIPTS = {"bump": "scripts/check-version-bump.py", "evidence": "scripts/release-evidence.py"}
BUILD_FILE = "android/app/build.gradle.kts"
SHIPPED_FILE = "android/app/src/main/java/com/you/hermeswidget/MainActivity.kt"
DOC = "docs/APK_RELEASE.md"
SHA = "9" * 64
SIZE = "7,263,575"


def git(repo: pathlib.Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else ""


def init(repo: pathlib.Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "gate@example.invalid")
    git(repo, "config", "user.name", "Gate")
    git(repo, "config", "commit.gpgsign", "false")


def commit_all(repo: pathlib.Path, message: str) -> str:
    """Commit everything and return the new sha. Asserts the commit happened: a silent
    no-op here produced a test that exercised the wrong state for an hour."""
    before = git(repo, "rev-parse", "HEAD")
    git(repo, "add", "-A")
    result = subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=repo, capture_output=True, text=True
    )
    after = git(repo, "rev-parse", "HEAD")
    if result.returncode != 0 or after == before:
        raise AssertionError(
            f"commit {message!r} did not happen: {result.stderr.strip() or 'no change'}"
        )
    return after


def install_scripts(repo: pathlib.Path) -> None:
    for relative in SCRIPTS.values():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO / relative, target)


def doc_text(commit: str, version: str = "0.4.3", code: int = 7, size: str = SIZE) -> str:
    return (
        "# release\n\n"
        "| Version | Commit | Size | SHA-256 |\n| --- | --- | --- | --- |\n"
        f"| `{version}` (versionCode {code}) | `{commit}` | {size} bytes | `{SHA}` |\n"
    )


def run(repo: pathlib.Path, which: str, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["python3", str(repo / SCRIPTS[which]), *args],
        cwd=repo, capture_output=True, text=True, check=False,
        env={**os.environ, "HERMES_WIDGET_DIR": str(repo / "_wd")},
    )
    return result.returncode, result.stdout + result.stderr


class ReleaseGateHarness(unittest.TestCase):
    """A repository with two commits, an origin, and a shallow and a full clone of it."""

    def setUp(self):
        root = pathlib.Path(tempfile.mkdtemp(prefix="hermes-gates-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)

        # The upstream a CI checkout would see: two commits, second one bumps 6 -> 7 and
        # the document records the first.
        self.upstream = root / "upstream"
        init(self.upstream)
        install_scripts(self.upstream)
        build = self.upstream / BUILD_FILE
        build.parent.mkdir(parents=True, exist_ok=True)
        build.write_text('        versionCode = 6\n        versionName = "0.4.2"\n')
        shipped = self.upstream / SHIPPED_FILE
        shipped.parent.mkdir(parents=True, exist_ok=True)
        shipped.write_text("// v6\n")
        recorded = commit_all(self.upstream, "v6")
        build.write_text('        versionCode = 7\n        versionName = "0.4.3"\n')
        shipped.write_text("// v7\n")
        doc = self.upstream / DOC
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(doc_text(recorded[:12]))
        self.head = commit_all(self.upstream, "v7")

        # `origin/main` exists and equals HEAD: the shape of a push to main.
        remote = root / "remote.git"
        subprocess.run(
            ["git", "clone", "-q", "--bare", str(self.upstream), str(remote)], check=True
        )
        git(self.upstream, "remote", "add", "origin", str(remote))
        git(self.upstream, "push", "-q", "origin", "main")
        self.upstream_main = git(self.upstream, "rev-parse", "origin/main")

        self.full = self._clone(root, depth=None)
        self.shallow = self._clone(root, depth=1)

    def _clone(self, root: pathlib.Path, depth: int | None) -> pathlib.Path:
        name = "full" if depth is None else "shallow"
        target = root / f"{name}-clone"
        args = ["git", "clone", "-q", "--branch", "main"]
        if depth is not None:
            args += ["--depth", str(depth)]
        args += [f"file://{root / 'remote.git'}", str(target)]
        subprocess.run(args, check=True, capture_output=True)
        # A clone inherits no identity, and `git commit` then fails — silently, because
        # the helper that commits does not check its exit code. Set it here so a commit in
        # these fixtures always happens.
        git(target, "config", "user.email", "gate@example.invalid")
        git(target, "config", "user.name", "Gate")
        install_scripts(target)
        return target

    def setUpFull(self) -> pathlib.Path:  # readable alias
        return self.full

    # --- the harness failures, in both clone shapes -------------------------

    def test_the_shallow_clone_really_is_shallow(self):
        self.assertEqual(git(self.full, "rev-list", "--count", "HEAD"), "2")
        self.assertEqual(git(self.shallow, "rev-list", "--count", "HEAD"), "1")

    def test_evidence_check_passes_in_a_full_clone(self):
        code, out = run(self.full, "evidence", "--check", "--doc", DOC)
        self.assertEqual(code, 0, out)
        self.assertIn("1 commit(s) behind HEAD", out)

    def test_evidence_check_names_a_shallow_clone_instead_of_the_document(self):
        code, out = run(self.shallow, "evidence", "--check", "--doc", DOC)
        self.assertEqual(code, 1, out)
        self.assertIn("shallow", out.lower())
        self.assertIn("fetch-depth", out)
        # It must not claim the document is wrong: that is what sent the next reader
        # hunting through APK_RELEASE.md.
        self.assertNotIn("worse than none", out)

    def test_version_bump_check_refuses_to_pass_vacuously_when_it_cannot_run(self):
        code, out = run(self.shallow, "bump")
        self.assertEqual(code, 1, out)
        self.assertIn("shallow", out.lower())
        self.assertIn("fetch-depth", out)

    def test_version_bump_check_is_real_on_a_push_to_main(self):
        # origin/main == HEAD here, which is the push shape. An empty diff at that base
        # would report success without checking anything.
        self.assertEqual(self.upstream_main, self.head)
        self.assertEqual(git(self.full, "rev-parse", "origin/main"), self.head)
        code, out = run(self.full, "bump")
        self.assertEqual(code, 0, out)
        self.assertIn("versionCode 6 -> 7", out)

    # --- and the failures the gates exist to catch ---------------------------

    def test_a_shipped_change_without_a_bump_fails(self):
        # Committed, because that is the state CI checks out. `--base` names the previous
        # commit explicitly, which is the script's documented escape hatch and keeps this
        # independent of how a clone's origin refs happen to be laid out.
        (self.full / SHIPPED_FILE).write_text("// v7 plus an unbumped change\n")
        commit_all(self.full, "tweak without a bump")
        code, out = run(self.full, "bump", "--base", "HEAD~1")
        self.assertEqual(code, 1, out)
        self.assertIn("versionCode is still", out)

    def test_the_same_change_with_a_bump_passes(self):
        (self.full / SHIPPED_FILE).write_text("// v7 plus a bumped change\n")
        (self.full / BUILD_FILE).write_text('        versionCode = 8\n        versionName = "0.4.4"\n')
        commit_all(self.full, "tweak with a bump")
        code, out = run(self.full, "bump", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("versionCode 7 -> 8", out)

    def test_docs_only_changes_never_need_a_bump(self):
        (self.full / "README.md").parent.mkdir(exist_ok=True)
        (self.full / "README.md").write_text("# docs\n")
        (self.full / DOC).write_text(doc_text(self.head[:12], size="9,999"))
        commit_all(self.full, "docs only")
        code, out = run(self.full, "bump", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("no shipped-app change", out)

    def test_a_version_mismatch_fails_the_evidence_check(self):
        (self.full / DOC).write_text(doc_text(self.head[:12], version="0.4.9", code=9))
        code, out = run(self.full, "evidence", "--check", "--doc", DOC)
        self.assertEqual(code, 1, out)
        self.assertIn("versionCode", out)

    def test_a_row_at_head_is_not_behind(self):
        (self.full / DOC).write_text(doc_text(self.head[:12]))
        code, out = run(self.full, "evidence", "--check", "--doc", DOC)
        self.assertEqual(code, 0, out)
        self.assertNotIn("behind HEAD", out)

    def test_a_commit_outside_the_history_fails(self):
        (self.full / DOC).write_text(doc_text("deadbeefcafe"))
        code, out = run(self.full, "evidence", "--check", "--doc", DOC)
        self.assertEqual(code, 1, out)
        self.assertIn("not in this history", out)

    def test_a_missing_row_fails_and_names_the_generator(self):
        (self.full / DOC).write_text("# release\n\nnothing here yet\n")
        code, out = run(self.full, "evidence", "--check", "--doc", DOC)
        self.assertEqual(code, 1, out)
        self.assertIn("release-evidence.py", out)

    def test_size_and_digest_are_provenance_never_a_gate(self):
        # A recorded size nothing could reproduce, plus a real artifact to measure. It
        # passes, and says the numbers are provenance.
        (self.full / DOC).write_text(doc_text(self.head[:12], size="1,111,111"))
        apk = self.full / "app-debug.apk"
        apk.write_bytes(b"not really an apk, but the check only reads bytes")
        code, out = run(self.full, "evidence", "--check", "--doc", DOC, "--apk", "app-debug.apk")
        self.assertEqual(code, 0, out)
        self.assertIn("provenance", out.lower())
        self.assertIn("1,111,111", out)
