"""Regression tests for unstable selection, relocated spans and CLI failures."""
import contextlib
import importlib.util
import io
import itertools
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SelectionTests(unittest.TestCase):
    def test_locate_is_independent_of_equal_hit_file_order(self):
        locator = load_script("03_locate.py")
        code = "void target() {\n  // stable comment\n  work();\n}"
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".git").mkdir()
            for name in ("A.java", "Z.java"):
                (directory / name).write_text("class Example {\n" + code + "\n}\n")
            hits = [(name, 2, "void target() {") for name in ("A.java", "Z.java")]
            results = []
            with patch.object(locator, "repo_dir", return_value=directory), \
                    patch.object(locator, "head_of", return_value="a" * 40):
                for order in itertools.permutations(hits):
                    with patch.object(locator, "grep", return_value=list(order)):
                        results.append(locator.locate("example/repo", {"id": 1, "original_code": code}, []))
            self.assertEqual(results[0], results[1])


class CrossrepoHitTests(unittest.TestCase):
    def test_hit_path_keeps_colons_in_comment_text(self):
        crossrepo = load_script("12_crossrepo.py")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "owner__repo" / "src" / "A.java"
            source.parent.mkdir(parents=True)
            source.write_text("class A { // TODO: x:y\n}\n")
            with patch.object(crossrepo, "REPOS", root), \
                    patch.object(crossrepo, "WORK", root):
                hits = crossrepo.global_hits(["TODO: x:y"])
            self.assertEqual(
                hits,
                [("owner/repo", "src/A.java", 1, "class A { // TODO: x:y")],
            )


class CrossfileTests(unittest.TestCase):
    def run_search(self, source):
        crossfile = load_script("21_crossfile.py")
        text = "// a particularly long first comment\n// last comment"
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / ".git").mkdir()
            (directory / "Copy.java").write_text(source)
            output = directory / "functions_located.jsonl"
            output.write_text(json.dumps({"id": 1, "repo": "example/repo", "status": "ok",
                                          "comments": [{"comment_id": 0, "comment": text, "lines": []}]}) + "\n")
            with patch.object(crossfile, "OUT", directory), \
                    patch.object(crossfile, "repo_dir", return_value=directory), \
                    patch.object(crossfile, "blame", return_value={"sha": "a" * 40}), \
                    patch.object(crossfile.subprocess, "run", return_value=SimpleNamespace(stdout="Copy.java\n")), \
                    patch.object(sys, "argv", ["21_crossfile.py"]), contextlib.redirect_stdout(io.StringIO()):
                crossfile.main()
            return json.loads(output.read_text())["comments"][0]

    def test_complete_span_includes_blank_lines(self):
        result = self.run_search("// unrelated\n// a particularly long first comment\n\n// last comment\n")
        self.assertEqual(result["lines"], [2, 3, 4])

    def test_code_text_cannot_complete_a_comment(self):
        result = self.run_search('// a particularly long first comment\nString value = "last comment";\n')
        self.assertEqual(result["lines"], [])

    def test_code_before_trailing_comment_is_not_comment_text(self):
        crossfile = load_script("21_crossfile.py")
        self.assertEqual(crossfile.relocated_span(
            "// last comment", ['String value = "last comment"; // unrelated']), [])


class ManualFixTests(unittest.TestCase):
    def test_head_fix_discards_stale_snapshot(self):
        fixes = load_script("25_manual_fixes.py")
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "A.java").write_text("// stable comment\n")
            output = directory / "functions_located.jsonl"
            output.write_text(json.dumps({"id": 1, "repo": "example/repo", "comments": [
                {"comment_id": 0, "at_commit": "b" * 40, "lines": [3]}]}) + "\n")
            with patch.object(fixes, "OUT", directory), \
                    patch.object(fixes, "repo_dir", return_value=directory), \
                    patch.object(fixes, "FIXES", [(1, 0, None, "A.java", 1, 1, "exact")]), \
                    patch.object(fixes, "blame", return_value={"sha": "a" * 40, "path": "old/A.java"}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(fixes.main(), 0)
            comment = json.loads(output.read_text())["comments"][0]
            self.assertNotIn("at_commit", comment)
            self.assertEqual(comment["path_snapshot"], "A.java")
            self.assertEqual(comment["path_at"], "old/A.java")
            self.assertEqual(comment["lines"], [1])


class VerificationExitTests(unittest.TestCase):
    def test_reproduction_stops_when_verification_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            executable = directory / "python3"
            executable.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALLS"\n'
                'case "$1" in scripts/05_verify.py) exit 1 ;; esac\n')
            executable.chmod(0o755)
            calls = directory / "calls.txt"
            environment = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", ""),
                               CALLS=str(calls))
            result = subprocess.run(["sh", str(SCRIPTS.parent / "reproduce.sh")],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1, result.stderr)
            invoked = calls.read_text().splitlines()
            self.assertEqual(invoked[-1], "scripts/05_verify.py --all-checks")
            self.assertNotIn("scripts/24_consistency.py", invoked)

    def test_failure_returns_nonzero_in_full_and_sample_modes(self):
        verifier = load_script("05_verify.py")
        row = dict(function_id="1", repo="repo", path="A.java", comment_line="1", match="exact",
                   function_ratio="1", commit_short="abc", commit_date="2026-10-31")
        with patch.object(verifier, "rows", return_value=[row]), \
                patch.object(verifier, "check_text", return_value="exact but differs"), \
                patch.object(verifier, "check_blame", return_value=None), \
                patch.object(verifier, "check_commit", return_value=None):
            for args in (["05_verify.py", "--full"], ["05_verify.py", "1"]):
                with self.subTest(args=args), patch.object(sys, "argv", args), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(verifier.main(), 1)

    def test_success_returns_zero_in_full_and_sample_modes(self):
        verifier = load_script("05_verify.py")
        row = dict(function_id="1", repo="repo", path="A.java", comment_line="1", match="exact",
                   function_ratio="1", commit_short="abc", commit_date="2026-10-31")
        with patch.object(verifier, "rows", return_value=[row]), \
                patch.object(verifier, "check_text", return_value=None), \
                patch.object(verifier, "check_blame", return_value=None), \
                patch.object(verifier, "check_commit", return_value=None):
            for args in (["05_verify.py", "--full"], ["05_verify.py", "1"]):
                with self.subTest(args=args), patch.object(sys, "argv", args), \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(verifier.main(), 0)

    def test_sample_fails_on_blame_or_commit_errors(self):
        verifier = load_script("05_verify.py")
        row = dict(function_id="1", repo="repo", path="A.java", comment_line="1", match="exact",
                   function_ratio="1", commit_short="abc", commit_date="2026-10-31")
        for failing_check in ("check_blame", "check_commit"):
            with self.subTest(check=failing_check), \
                    patch.object(verifier, "rows", return_value=[row]), \
                    patch.object(verifier, "check_text", return_value=None), \
                    patch.object(verifier, "check_blame", return_value=None), \
                    patch.object(verifier, "check_commit", return_value=None), \
                    patch.object(verifier, failing_check, return_value="verification failed"), \
                    patch.object(sys, "argv", ["05_verify.py", "1"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(verifier.main(), 1)

    def test_cli_exit_status_matches_real_text_verification(self):
        import csv

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            source = directory / "A.java"
            source.write_text("// stable comment\n")
            row = dict(function_id="1", repo="example/repo", path=str(source), path_snapshot="",
                       at_head="True", comment_line="1", n_comment_lines="1", match="exact",
                       commenttext="// stable comment", commit_id="a" * 40)
            command = ("import runpy,sys; from pathlib import Path; "
                       "sys.path.insert(0,sys.argv[1]); import common; "
                       "common.OUT=Path(sys.argv[2]); "
                       "sys.argv=['05_verify.py','--full']; "
                       "runpy.run_path(str(Path(common.__file__).parent/'05_verify.py'),run_name='__main__')")
            for text, expected in (("// stable comment", 0), ("// different comment", 1)):
                row["commenttext"] = text
                with (directory / "tesoro_provenance.csv").open("w", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=list(row))
                    writer.writeheader()
                    writer.writerow(row)
                result = subprocess.run([sys.executable, "-c", command, str(SCRIPTS), tmp],
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, expected, result.stderr)


if __name__ == "__main__":
    unittest.main()
