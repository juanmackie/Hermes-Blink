#!/usr/bin/env python3
"""Portable by design: the suite must not depend on the platform it runs on."""
from __future__ import annotations
import pathlib, re, unittest
TESTS_DIR = pathlib.Path(__file__).resolve().parent
_PY = "pyth" + "on"
_SCHEME = "file:" + "//"
_INTERPRETER_CALL = re.compile(r"subprocess\.(?:run|Popen|check_output|check_call)\(\s*\[\s*[\"']" + _PY + r"3?[\"']")
_FILE_URL_FROM_PATH = re.compile("f[\"']" + _SCHEME + r"|[\"']" + _SCHEME + r"\{")
_URI_ABSENT = "as_uri()"

def _sources() -> list[pathlib.Path]:
    return sorted(p for p in TESTS_DIR.glob("test_*.py") if p.name != pathlib.Path(__file__).name)

class PortableInterpreter(unittest.TestCase):
    def test_no_bare_interpreter_name(self) -> None:
        offenders = []
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            for hit in _INTERPRETER_CALL.finditer(text):
                line = text[:hit.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}")
        self.assertEqual(offenders, [])

    def test_sys_imported_for_executable_use(self) -> None:
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            if _INTERPRETER_CALL.search(text) or _PY + ".executable" not in text:
                continue
            imports = re.search(r"^((?:import |from ).*)$", text, re.MULTILINE)
            block = imports.group(1) if imports else ""
            self.assertRegex(block, r"import sys")

    def test_no_hand_built_file_url(self) -> None:
        offenders = []
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            for hit in _FILE_URL_FROM_PATH.finditer(text):
                line = text[:hit.start()].count("\n") + 1
                offenders.append(f"{path.name}:{line}")
        self.assertEqual(offenders, [])

    def test_local_remote_uses_as_uri(self) -> None:
        gates = (TESTS_DIR / "test_release_gates.py").read_text(encoding="utf-8")
        self.assertIn(_URI_ABSENT, gates)

class TheSuiteRuns(unittest.TestCase):
    def test_real_suite_size(self) -> None:
        discovered = unittest.defaultTestLoader.discover(str(TESTS_DIR), "test_*.py")
        count = discovered.countTestCases()
        self.assertGreater(count, 100, f"only {count} tests discovered")

if __name__ == "__main__":
    import sys
    sys.exit(0 if unittest.main(exit=False).result.wasSuccessful() else 1)
