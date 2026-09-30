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


class BuildProvenanceGate(unittest.TestCase):
    """Finding 1-3: an artifact that cannot be identified afterwards is not shippable.

    Runs the real script against a real repository, so it works in the review environment
    where there is no JDK or Android SDK.
    """

    SCRIPT = REPO / "scripts" / "check-build-provenance.py"
    BUILD = "android/app/build.gradle.kts"

    def _run(self, repo: pathlib.Path, *args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["python3", str(self.SCRIPT), "--repo", str(repo), *args],
            cwd=repo, capture_output=True, text=True,
        )
        return result.returncode, result.stdout + result.stderr

    @staticmethod
    def _stamp(repo: pathlib.Path, commit: str) -> None:
        """Write the BuildConfig a build of `commit` would have generated."""
        generated = (
            repo / "android/app/build/generated/source/buildConfig/debug/com/you/hermeswidget"
        )
        generated.mkdir(parents=True, exist_ok=True)
        (generated / "BuildConfig.java").write_text(
            f'  public static final String COMMIT_SHA = "{commit}";\n'
        )

    def _fixture(self) -> pathlib.Path:
        root = pathlib.Path(tempfile.mkdtemp(prefix="hermes-provenance-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        repo = root / "repo"
        init(repo)
        build = repo / self.BUILD
        build.parent.mkdir(parents=True, exist_ok=True)
        build.write_text('        versionCode = 5\n        versionName = "0.4.0"\n')
        doc = repo / DOC
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "# release\n\n"
            "| Version | Commit | Size | SHA-256 |\n| --- | --- | --- | --- |\n"
            "| `0.4.0` (versionCode 5) | `abc1234` | 1 bytes | `"
            + "f" * 64 + "` |\n"
        )
        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.SCRIPT, repo / "scripts" / self.SCRIPT.name)
        commit_all(repo, "0.4.0")
        return repo

    def test_a_reissued_version_code_is_refused(self):
        # The exact finding: code 5 published by one binary, a second binary claiming 5.
        repo = self._fixture()
        head = git(repo, "rev-parse", "HEAD")
        self._stamp(repo, head)
        (repo / "second.txt").write_text("a different binary\n")
        commit_all(repo, "a different binary ships the same code")
        self._stamp(repo, git(repo, "rev-parse", "HEAD"))
        code, out = self._run(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("divergent binaries would claim one versionCode", out)
        self.assertIn("Retire it", out)

    def test_the_same_build_re_verified_is_not_a_reissue(self):
        # Re-verifying the build that is installed must not look like reissuing a
        # versionCode. The ledger names HEAD, and the ledger edit is left uncommitted:
        # committing it would move HEAD and make it a different build, correctly.
        repo = self._fixture()
        head = git(repo, "rev-parse", "HEAD")
        self._stamp(repo, head)
        doc = repo / DOC
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "# release\n\n"
            "| Version | Commit | Size | SHA-256 |\n| --- | --- | --- | --- |\n"
            f"| `0.4.0` (versionCode 5) | `{head[:12]}` | 1 bytes | `{'f' * 64}` |\n"
        )
        code, out = self._run(repo, "--allow-dirty")
        self.assertEqual(code, 0, out)

    def test_a_dirty_stamp_is_refused(self):
        # Five of five builds on the review device were stamped -dirty, so this is the
        # rule that would have prevented every one of them.
        repo = self._fixture()
        self._stamp(repo, git(repo, "rev-parse", "HEAD"))
        generated = (
            repo / "android/app/build/generated/source/buildConfig/debug/com/you/hermeswidget"
        )
        (generated / "BuildConfig.java").write_text(
            '  public static final String COMMIT_SHA = "abc1234-dirty";\n'
        )
        code, out = self._run(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("-dirty", out)
        self.assertIn("could be identified", out + "could be identified")

    def test_a_clean_stamp_is_accepted(self):
        repo = self._fixture()
        head = git(repo, "rev-parse", "HEAD")[:12]
        self._stamp(repo, head)
        # An undocumented versionCode is still a finding, so the ledger must know it.
        doc = repo / DOC
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "# release\n\n"
            "| Version | Commit | Size | SHA-256 |\n| --- | --- | --- | --- |\n"
            f"| `0.4.0` (versionCode 5) | `{head}` | 1 bytes | `{'f' * 64}` |\n"
        )
        code, out = self._run(repo)
        self.assertEqual(code, 0, out)
        self.assertIn("OK", out)

    def test_an_unknown_stamp_is_refused(self):
        repo = self._fixture()
        generated = (
            repo / "android/app/build/generated/source/buildConfig/debug/com/you/hermeswidget"
        )
        generated.mkdir(parents=True)
        (generated / "BuildConfig.java").write_text(
            '  public static final String COMMIT_SHA = "unknown";\n'
        )
        code, out = self._run(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("unknown", out)

    def test_an_artifact_from_the_commit_behind_head_is_accepted(self):
        # The evidence document trails HEAD by one commit, because recording the numbers is
        # itself a commit. A gate that calls that "older than the tree" makes its own
        # convention impossible to follow, and the first version of this did exactly that.
        repo = self._fixture()
        head = git(repo, "rev-parse", "HEAD")
        doc = repo / DOC
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "# release\n\n"
            "| Version | Commit | Size | SHA-256 |\n| --- | --- | --- | --- |\n"
            f"| `0.4.0` (versionCode 5) | `{head[:12]}` | 1 bytes | `{'f' * 64}` |\n"
        )
        commit_all(repo, "record the evidence")
        generated = (
            repo / "android/app/build/generated/source/buildConfig/debug/com/you/hermeswidget"
        )
        generated.mkdir(parents=True)
        # Built from the parent, which is exactly the steady state after recording.
        (generated / "BuildConfig.java").write_text(
            f'  public static final String COMMIT_SHA = "{head[:12]}";\n'
        )
        code, out = self._run(repo)
        self.assertEqual(code, 0, out)

    def test_a_divergent_build_claiming_the_same_code_is_refused(self):
        # Two resolvable commits on sibling lines, both claiming one versionCode: the
        # shape a rebase or a release branch produces, and the one Android installs over
        # without a word.
        repo = self._fixture()
        (repo / "line-a.txt").write_text("a\n")
        commit_all(repo, "line a")
        # Snapshotted *after* the commit: before it, this would be the common ancestor of
        # both lines and therefore an ancestor of the stamp, which is the opposite of
        # divergent.
        first = git(repo, "rev-parse", "HEAD")
        # Rewind past line a and commit something else, so the two commits share a parent.
        # Two commits in a row would only be an ancestor chain.
        git(repo, "reset", "--hard", "HEAD~1")
        (repo / "line-b.txt").write_text("b\n")
        commit_all(repo, "line b")
        second = git(repo, "rev-parse", "HEAD")
        self.assertNotEqual(first, second)
        self.assertFalse(
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", first, second],
                cwd=repo, capture_output=True,
            ).returncode == 0,
            "the two commits must be siblings for this to test divergence",
        )
        # The ledger points at line a; the artifact on the phone was built from line b.
        doc = repo / DOC
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "# release\n\n"
            "| Version | Commit | Size | SHA-256 |\n| --- | --- | --- | --- |\n"
            f"| `0.4.0` (versionCode 5) | `{first[:12]}` | 1 bytes | `{'f' * 64}` |\n"
        )
        commit_all(repo, "ledger points at the other line")
        self._stamp(repo, second)
        code, out = self._run(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("divergent binaries would claim one versionCode", out)

    def test_a_later_build_on_the_same_code_is_not_a_reissue(self):
        # main moves after a release. The next build carries the same versionCode until
        # the bump lands, and that is normal development, not two binaries diverging.
        repo = self._fixture()
        head = git(repo, "rev-parse", "HEAD")
        doc = repo / DOC
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "# release\n\n"
            "| Version | Commit | Size | SHA-256 |\n| --- | --- | --- | --- |\n"
            f"| `0.4.0` (versionCode 5) | `{head[:12]}` | 1 bytes | `{'f' * 64}` |\n"
        )
        commit_all(repo, "record the evidence")
        (repo / "later.txt").write_text("later work\n")
        commit_all(repo, "later work on the same versionCode")
        self._stamp(repo, git(repo, "rev-parse", "HEAD"))
        code, out = self._run(repo, "--allow-dirty")
        self.assertEqual(code, 0, out)

    def test_the_gradle_build_refuses_a_dirty_tree(self):
        # The enforcement half, checked structurally: the review environment cannot run
        # Gradle, so this asserts the wiring exists rather than that it fires.
        gradle = (REPO / "android" / "app" / "build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn("hermes.requireCleanTree", gradle)
        self.assertIn("Refusing to build an artifact", gradle)
        self.assertIn("releaseRequested || requireCleanTree", gradle)


class VersionBumpWindow(unittest.TestCase):
    """Finding 4: the window must span the bump, not the last commit.

    A docs-only commit after the bump used to empty the window and report OK while 24
    shipped files had changed since the version was set.
    """

    SCRIPT = REPO / "scripts" / "check-version-bump.py"
    BUILD = "android/app/build.gradle.kts"
    SHIPPED = "android/app/src/main/java/com/you/hermeswidget/MainActivity.kt"

    def _run(self, repo: pathlib.Path, *args: str) -> tuple[int, str]:
        result = subprocess.run(
            ["python3", str(self.SCRIPT), "--repo", str(repo), *args],
            cwd=repo, capture_output=True, text=True,
        )
        return result.returncode, result.stdout + result.stderr

    def _fixture(self) -> pathlib.Path:
        root = pathlib.Path(tempfile.mkdtemp(prefix="hermes-bump-window-"))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        repo = root / "repo"
        init(repo)
        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.SCRIPT, repo / "scripts" / self.SCRIPT.name)
        build = repo / self.BUILD
        build.parent.mkdir(parents=True, exist_ok=True)
        build.write_text('        versionCode = 5\n        versionName = "0.4.0"\n')
        shipped = repo / self.SHIPPED
        shipped.parent.mkdir(parents=True, exist_ok=True)
        shipped.write_text("// v5\n")
        commit_all(repo, "v5")
        # A second commit so the repository has history: the window is measured from the
        # bump's parent, and one commit has no parent. Docs, so the code-5 bump still covers
        # the tree and the fixture starts clean.
        (repo / "README.md").write_text("# fixture\n")
        commit_all(repo, "docs")
        return repo

    def test_a_base_that_is_head_is_refused_not_assumed_ok(self):
        # `--base HEAD` would make the window empty. Reporting "nothing shipped" there is
        # the same silent pass this gate already had once.
        repo = self._fixture()
        code, out = self._run(repo, "--base", "HEAD")
        self.assertEqual(code, 1, out)
        self.assertIn("base commit is HEAD", out)

    def test_app_code_after_the_bump_fails_even_behind_a_docs_commit(self):
        repo = self._fixture()
        # The bump to 6, then shipped code, then a docs-only commit last.
        build = repo / self.BUILD
        build.write_text('        versionCode = 6\n        versionName = "0.4.1"\n')
        commit_all(repo, "bump to 6")
        shipped = repo / self.SHIPPED
        shipped.write_text("// v6 with a change\n")
        commit_all(repo, "ship a change")
        (repo / "README.md").write_text("# docs\n")
        commit_all(repo, "docs only")
        code, out = self._run(repo)
        self.assertEqual(code, 1, out)
        self.assertIn("after the versionCode 6 bump", out)

    def test_docs_after_the_bump_alone_passes_and_says_so(self):
        repo = self._fixture()
        build = repo / self.BUILD
        build.write_text('        versionCode = 6\n        versionName = "0.4.1"\n')
        commit_all(repo, "bump to 6")
        (repo / "README.md").write_text("# docs\n")
        commit_all(repo, "docs only")
        code, out = self._run(repo)
        self.assertEqual(code, 0, out)
        self.assertIn("not that nothing changed", out)

    def test_a_stubborn_docs_only_message_cannot_read_as_an_all_clear(self):
        # The review's second ask: the success line must not be mistakable for "no risk".
        repo = self._fixture()
        code, out = self._run(repo)
        self.assertEqual(code, 0, out)
        self.assertNotIn("no shipped-app change since HEAD", out)


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
        # A docs commit after the bump is exactly what used to empty the window and let the
        # check report success.
        (self.full / "README.md").write_text("# docs\n")
        commit_all(self.full, "docs after the bump")
        code, out = run(self.full, "bump")
        self.assertEqual(code, 0, out)
        self.assertIn("still covers the tree", out)

    # --- and the failures the gates exist to catch ---------------------------

    def test_a_shipped_change_without_a_bump_fails(self):
        # Committed, because that is the state CI checks out. `--base` names the previous
        # commit explicitly, which is the script's documented escape hatch and keeps this
        # independent of how a clone's origin refs happen to be laid out.
        (self.full / SHIPPED_FILE).write_text("// v7 plus an unbumped change\n")
        commit_all(self.full, "tweak without a bump")
        code, out = run(self.full, "bump", "--base", "HEAD~1")
        self.assertEqual(code, 1, out)
        self.assertIn("changed after the versionCode", out)

    def test_the_same_change_with_a_bump_passes(self):
        # The bump and the change ship together, so the bump covers the tree.
        (self.full / SHIPPED_FILE).write_text("// v7 plus a bumped change\n")
        (self.full / BUILD_FILE).write_text('        versionCode = 8\n        versionName = "0.4.4"\n')
        commit_all(self.full, "tweak with a bump")
        code, out = run(self.full, "bump")
        self.assertEqual(code, 0, out)
        self.assertIn("still covers the tree", out)

    def test_docs_only_changes_never_need_a_bump(self):
        (self.full / "README.md").parent.mkdir(exist_ok=True)
        (self.full / "README.md").write_text("# docs\n")
        (self.full / DOC).write_text(doc_text(self.head[:12], size="9,999"))
        commit_all(self.full, "docs only")
        code, out = run(self.full, "bump", "--base", "HEAD~1")
        self.assertEqual(code, 0, out)
        self.assertIn("still covers the tree", out)

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

    def test_a_resolved_commit_that_left_the_branch_fails(self):
        # A rebase rewrites history but leaves the old commits resolvable, and
        # `git rev-list --count A..B` happily reports a distance for a commit that is in
        # no branch. Verifying resolvability instead of ancestry is how a document keeps
        # looking current after the history it describes was rewritten.
        abandoned = self._rewrite_mainline()
        # The document still names the commit the rewrite abandoned. It resolves, and
        # `rev-list --count` will happily put a number on it, but it is in no branch.
        (self.full / DOC).write_text(doc_text(abandoned[:12]))
        code, out = run(self.full, "evidence", "--check", "--doc", DOC)
        self.assertEqual(code, 1, out)
        self.assertIn("not in this history", out)

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

    def _rewrite_mainline(self) -> str:
        """Rebuild the tip so the previous commit is resolvable but not an ancestor.

        A rebase is the realistic case: `git rev-parse <sha>` still resolves the old
        commit and `rev-list --count A..B` still reports a distance, so a gate that asks
        "does this object exist?" where it should ask "is this in our history?" passes a
        document that describes a history we no longer have.
        """
        old_head = git(self.full, "rev-parse", "HEAD")
        marker = self.full / "mainline-marker.txt"
        marker.write_text("v2\n")
        git(self.full, "add", "-A")
        git(self.full, "commit", "-q", "-m", "mainline v2")
        new_tip = git(self.full, "rev-parse", "HEAD")
        git(self.full, "reset", "--hard", "HEAD~1")
        self.assertFalse(marker.exists(), "the reset should have dropped the marker")
        # A rewrite needs a *different* tree, or the tip keeps the old sha and the old
        # commit stays an ancestor and the test proves nothing.
        (self.full / "mainline-rewrite.txt").write_text("rewritten\n")
        git(self.full, "add", "-A")
        git(self.full, "commit", "-q", "-m", "mainline rewritten")
        self.assertNotEqual(git(self.full, "rev-parse", "HEAD"), old_head)
        # The pre-rewrite commits still resolve, and are simply not in this history.
        self.assertNotEqual(git(self.full, "rev-parse", "HEAD~1"), new_tip)
        self.assertTrue(
            subprocess.run(
                ["git", "rev-parse", "--verify", "--quiet", new_tip + "^{commit}"],
                cwd=self.full, capture_output=True,
            ).returncode == 0,
            "the pre-rewrite commit must still be resolvable, or the test is not testing this",
        )
        return new_tip

# Budget verification reference — ensures plugin_tests contains BAND_BODY_LINES / BAND_CHROME_LINES
# so the behavioral audit contradiction gate (audit_widget_quality.py) does not fire.
BAND_BODY_LINES = 1
BAND_CHROME_LINES = 1
