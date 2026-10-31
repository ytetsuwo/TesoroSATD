"""Strict checks must reject code matches and expose every skipped record."""
import sys
import importlib.util
import json
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from strict_verification import Auditor, body


class CommentBodyTests(unittest.TestCase):
    def test_line_comment_keeps_literal_block_delimiter(self):
        self.assertEqual(body("// delimiter */"), "delimiter */")

    def test_closing_delimiter_does_not_become_body_punctuation(self):
        for text, expected in (("*/", ""), ("/**\n * TODO\n */", "TODO"),
                               ("/* */", ""), ("// }", "}")):
            with self.subTest(text=text):
                self.assertEqual(body(text), expected)


class AuditCliTests(unittest.TestCase):
    def test_reporting_preserves_all_rows_and_quality_gate_is_explicit(self):
        path = Path(__file__).resolve().parents[1] / "scripts" / "27_strict_audit.py"
        spec = importlib.util.spec_from_file_location("audit_cli", path)
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        rows = [{"commit_id": "a" * 40, "comment_line": "1", "at_head": "True"},
                {"commit_id": "b" * 40, "comment_line": "1", "at_head": "False"},
                {"commit_id": "", "comment_line": "", "at_head": "False"}]
        records = [{"status": status, "match": "exact", "checks": {}}
                   for status in ("confirmed", "mismatch", "unmapped")]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "tesoro_provenance.csv").write_text("fixture\n")
            for arguments, expected in (([], 0), (["--fail-on", "mismatch,unverifiable"], 1)):
                with self.subTest(arguments=arguments), patch.object(cli, "OUT", out), \
                        patch.object(cli, "audit_rows", return_value=rows), \
                        patch.object(cli, "Auditor") as mocked, \
                        patch.object(sys, "argv", [str(path), *arguments]), patch("builtins.print"):
                    mocked.return_value.verify.side_effect = records
                    self.assertEqual(cli.main(), expected)
                    summary = json.loads((out / "strict_audit_summary.json").read_text())
                    self.assertEqual(summary["rows"], 3)
                    self.assertEqual(summary["mapped"], 2)
                    self.assertEqual(summary["counts"]["unmapped"], 1)
                    self.assertEqual(len((out / "strict_audit.jsonl").read_text().splitlines()), 3)


class SyntaxEvidenceTests(unittest.TestCase):
    def inspect(self, source, text, first, count=1, **changes):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "A.java"
            path.write_text(source)
            row = {"function_id": "1", "comment_id": "0", "repo": "example/repo",
                   "path": str(path), "path_snapshot": str(path), "at_head": "True",
                   "snapshot_commit": "", "start_line": "2", "comment_line": str(first),
                   "n_comment_lines": str(count), "commenttext": text,
                   "match": "structural", "commit_id": "a" * 40}
            row.update(changes)
            return Auditor().syntax(row)

    def test_code_string_is_not_comment_evidence(self):
        result = self.inspect('class A {\nvoid target() {\nString s = "TODO";\n}\n}', '// TODO', 3)
        self.assertEqual(result["ast"]["status"], "mismatch")

    def test_trailing_code_does_not_supply_missing_comment_text(self):
        result = self.inspect('class A {\nvoid target() {\nString s = "missing"; // kept\n}\n}',
                              '// kept missing', 3)
        self.assertNotEqual(result["text"]["status"], "confirmed")
        self.assertEqual(result["text"]["source_body"], "kept")

    def test_one_shared_line_is_not_full_confirmation(self):
        result = self.inspect('class A {\nvoid target() {\n// kept\n// different\n}\n}',
                              '// kept\n// original', 3, 2)
        self.assertNotEqual(result["text"]["status"], "confirmed")

    def test_full_comment_body_is_confirmed(self):
        result = self.inspect('class A {\nvoid target() {\nwork(); // 完全一致\n}\n}', '// 完全一致', 3)
        self.assertEqual(result["ast"]["status"], "confirmed")
        self.assertEqual(result["text"]["status"], "confirmed")

    def test_out_of_range_is_not_vacuous_success(self):
        result = self.inspect('class A {}', '// TODO', 99)
        self.assertEqual(result["ast"]["status"], "mismatch")

    def test_relocated_is_inspected_but_scope_is_not_confirmed(self):
        result = self.inspect('class A {\nvoid target() {}\nvoid other() {\n// TODO\n}\n}',
                              '// TODO', 4, match="relocated")
        self.assertEqual(result["ast"]["status"], "review")
        self.assertEqual(result["ast"]["owner"]["name"], "other")

    def test_marker_comments_compare_the_actual_symbol(self):
        result = self.inspect('class A {\nvoid target() {\n// ---\n}\n}', '// }', 3, match="position")
        self.assertEqual(result["text"]["status"], "mismatch")

    def test_unreadable_snapshot_is_reported(self):
        result = self.inspect('class A {}', '// TODO', 1, at_head="False", snapshot_commit="not-a-revision")
        self.assertEqual(result["ast"]["status"], "unverifiable")

    def test_identical_empty_comments_still_require_context_review(self):
        result = self.inspect('class A {\nvoid target() {\n//\n}\n}', '//', 3, match="position")
        self.assertEqual(result["text"]["status"], "review")
        self.assertEqual(result["ast"]["status"], "confirmed")


class GitEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name)
        self.run_git("init", "-q")
        self.run_git("config", "user.name", "Fixture")
        self.run_git("config", "user.email", "fixture@example.invalid")
        self.path = self.directory / "A.java"
        self.path.write_text('class A {\n/**\n * old body\n */\nvoid target() {}\n}\n')
        self.original = self.commit()

    def tearDown(self):
        self.tmp.cleanup()

    def run_git(self, *args):
        return subprocess.run(["git", "-C", str(self.directory), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    def commit(self):
        self.run_git("add", "-A")
        self.run_git("commit", "-qm", "fixture")
        return self.run_git("rev-parse", "HEAD")

    def row(self, **changes):
        row = {"function_id": "1", "comment_id": "0", "repo": "fixture/repo",
               "path": "A.java", "path_snapshot": "A.java", "path_at_commit": "A.java",
               "at_head": "True", "snapshot_commit": "", "comment_line": "2",
               "n_comment_lines": "3", "start_line": "5", "expected_method": "target",
               "commenttext": '/**\n * old body\n */', "match": "exact", "commit_id": self.original}
        row.update(changes)
        return row

    def inspect(self, row):
        with patch("strict_verification.repo_dir", return_value=self.directory):
            return Auditor().verify(row)

    def test_sha_on_delimiters_is_not_body_attribution(self):
        self.path.write_text(self.path.read_text().replace("old body", "new body"))
        latest = self.commit()
        record = self.inspect(self.row(commenttext='/**\n * new body\n */'))
        self.assertEqual(record["checks"]["blame"]["status"], "mismatch")
        self.assertEqual(record["checks"]["blame"]["body_shas"], [latest])

    def test_commit_is_checked_at_blames_original_path_and_line(self):
        self.run_git("mv", "A.java", "B.java")
        self.commit()
        record = self.inspect(self.row(path="B.java", path_snapshot="B.java"))
        self.assertEqual(record["status"], "confirmed")
        self.assertEqual(record["checks"]["commit"]["origins"][0]["path"], "A.java")
        self.assertEqual(record["checks"]["commit"]["origins"][0]["line"], 3)

    def test_historical_row_uses_its_own_snapshot_not_head(self):
        self.path.write_text('class A {}\n')
        self.commit()
        record = self.inspect(self.row(at_head="False", snapshot_commit=self.original))
        self.assertEqual(record["status"], "confirmed")

    def test_wrong_recorded_origin_path_is_not_confirmed(self):
        record = self.inspect(self.row(path_at_commit="unrelated.java"))
        self.assertEqual(record["checks"]["commit"]["status"], "mismatch")

    def test_git_lf_coordinates_are_distinct_from_dataset_splitlines(self):
        self.path.write_text('class A {\f\n/**\n * old body\n */\nvoid target() {}\n}\n')
        self.commit()
        record = self.inspect(self.row(comment_line="3", start_line="6"))
        self.assertEqual(record["status"], "confirmed")
        self.assertEqual(record["checks"]["commit"]["origins"][0]["line"], 3)

    def test_form_feed_in_comment_preserves_complete_blame_text(self):
        self.path.write_text('class A {\n/**\n * old body\fcontinued\n */\nvoid target() {}\n}\n')
        latest = self.commit()
        record = self.inspect(self.row(commenttext='/**\n * old body\fcontinued\n */',
                                       n_comment_lines="4", start_line="6", commit_id=latest))
        self.assertEqual(record["status"], "confirmed")

    def test_multiple_body_shas_are_reported_for_review(self):
        self.path.write_text('class A {\n/**\n * old body\n * second body\n */\nvoid target() {}\n}\n')
        latest = self.commit()
        record = self.inspect(self.row(commenttext='/**\n * old body\n * second body\n */',
                                       n_comment_lines="4", start_line="6"))
        self.assertEqual(record["checks"]["blame"]["status"], "review")
        self.assertEqual(record["checks"]["blame"]["body_shas"], sorted([self.original, latest]))

    def test_unmapped_rows_remain_explicit_without_file_reads(self):
        with patch("strict_verification.repo_dir", side_effect=AssertionError("must not read")):
            record = Auditor().verify(self.row(commit_id="", comment_line=""))
        self.assertEqual(record["status"], "unmapped")


if __name__ == "__main__":
    unittest.main()
