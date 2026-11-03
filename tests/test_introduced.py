"""Introduction commits must follow history, not just the last writer."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_introduced():
    spec = importlib.util.spec_from_file_location("introduced", SCRIPTS / "28_introduced.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntroductionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.path = self.directory / "A.java"
        self.module = load_introduced()

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.directory), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self, text):
        self.path.write_text(text)
        self.git("add", "A.java")
        self.git("commit", "-qm", "fixture")
        return self.git("rev-parse", "HEAD")

    def row(self, text, line, count=1, sha=None, path="A.java"):
        return {"function_id": "1", "comment_id": "0", "repo": "fixture/repo",
                "path": path, "path_snapshot": path, "path_at_commit": path,
                "at_head": "True", "snapshot_commit": "", "start_line": "1",
                "comment_line": str(line), "n_comment_lines": str(count),
                "commenttext": text, "match": "exact", "commit_id": sha or ""}

    def analyse(self, row, audit=None):
        with patch.object(self.module, "repo_dir", return_value=self.directory), \
                patch("strict_verification.repo_dir", return_value=self.directory):
            return self.module.Introducer().analyse(
                row, audit or {"checks": {"text": {"status": "confirmed"}}})

    def test_unchanged_comment_is_introduced_in_the_first_commit(self):
        first = self.commit("class A {\n// stable SATD\nvoid target() {}\n}\n")
        result = self.analyse(self.row("// stable SATD", 2, sha=first))
        self.assertEqual(result["status"], "introduced")
        self.assertEqual(result["introduced_in"], first)
        self.assertEqual(result["basis"], "blame")

    def test_later_wording_change_is_not_the_first_commit(self):
        self.commit("class A {\n// old wording\nvoid target() {}\n}\n")
        latest = self.commit("class A {\n// new wording\nvoid target() {}\n}\n")
        result = self.analyse(self.row("// new wording", 2, sha=latest))
        self.assertEqual(result["status"], "introduced")
        self.assertEqual(result["introduced_in"], latest)

    def test_multiline_comment_uses_the_commit_that_completed_the_body(self):
        first = self.commit("class A {\n// first line\nvoid target() {}\n}\n")
        latest = self.commit("class A {\n// first line\n// second line\nvoid target() {}\n}\n")
        result = self.analyse(self.row("// first line\n// second line", 2, 2, sha=latest))
        self.assertEqual(result["status"], "introduced")
        self.assertEqual(result["introduced_in"], latest)
        self.assertNotEqual(result["introduced_in"], first)

    def test_file_rename_does_not_create_a_new_introduction(self):
        first = self.commit("class A {\n// stable SATD\nvoid target() {}\n}\n")
        self.git("mv", "A.java", "B.java")
        self.git("commit", "-qm", "rename")
        result = self.analyse(self.row("// stable SATD", 2, sha=first, path="B.java"))
        self.assertEqual(result["status"], "introduced")
        self.assertEqual(result["introduced_in"], first)

    def test_reviewed_text_is_not_given_an_introduction_commit(self):
        self.commit("class A {}\n")
        result = self.analyse(
            self.row("// uncertain", 1, sha="a" * 40),
            {"checks": {"text": {"status": "review", "source_body": "",
                                 "similarity": 0}}})
        self.assertEqual(result["status"], "not_confirmed")
        self.assertNotIn("introduced_in", result)
        self.assertEqual(result["dataset_status"], "not_found")

    def test_dataset_body_is_found_in_older_history(self):
        first = self.commit("class A {\n// old wording\nvoid target() {}\n}\n")
        latest = self.commit("class A {\n// new wording\nvoid target() {}\n}\n")
        result = self.analyse(
            self.row("// old wording", 2, sha=latest),
            {"checks": {"text": {"status": "review", "source_body": "new wording",
                                 "similarity": 0.6}}})
        self.assertEqual(result["status"], "not_confirmed")
        self.assertEqual(result["dataset_status"], "historical")
        self.assertEqual(result["dataset_introduced_in"], first)

    def test_dataset_prefix_uses_the_longer_source_comment(self):
        first = self.commit(
            "class A {\n// HAS TO BE REINITIALIZED... don't ask why.\nvoid target() {}\n}\n")
        result = self.analyse(
            self.row("// HAS TO BE REINITIALIZED", 2, sha=first),
            {"checks": {"text": {"status": "review",
                                 "reason": "recorded body is a prefix of a longer comment",
                                 "source_body": "HAS TO BE REINITIALIZED... don't ask why.",
                                 "similarity": 0.7}}})
        self.assertEqual(result["status"], "not_confirmed")
        self.assertEqual(result["dataset_status"], "prefix")
        self.assertEqual(result["dataset_introduced_in"], first)

    def test_symbol_body_uses_code_context_with_a_note(self):
        first = self.commit("class A {\n// }\nvoid target() {}\n}\n")
        result = self.analyse(
            self.row("// }", 2, sha=first),
            {"checks": {"text": {"status": "review", "source_body": "}",
                         "similarity": 0}}})
        self.assertEqual(result["status"], "not_confirmed")
        self.assertEqual(result["dataset_status"], "context")
        self.assertEqual(result["dataset_introduced_in"], first)
        self.assertIn("周辺コード", result["dataset_note"])

    def test_empty_comment_uses_code_context_with_a_note(self):
        first = self.commit("class A {\n//\nvoid target() {}\n}\n")
        result = self.analyse(
            self.row("//", 2, sha=first),
            {"checks": {"text": {"status": "review", "source_body": "",
                                 "similarity": 0}}})
        self.assertEqual(result["status"], "not_confirmed")
        self.assertEqual(result["dataset_status"], "context")
        self.assertEqual(result["dataset_introduced_in"], first)
        self.assertIn("空コメント", result["dataset_note"])

    def test_missing_provenance_is_unmapped(self):
        with patch.object(self.module, "repo_dir", side_effect=AssertionError("must not read")):
            result = self.module.Introducer().analyse(self.row("// none", 1), None)
        self.assertEqual(result["status"], "unmapped")


if __name__ == "__main__":
    unittest.main()
