"""Characterization tests: optimize implementation, not matching decisions."""
import importlib.util
import hashlib
import json
import random
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import astjava
import git_auth
import provenance_checks
from comment_matching import map_comment
from source_files import SourceFiles
from source_lines import LineIndex


def load_script(filename):
    spec = importlib.util.spec_from_file_location(filename, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


locator = load_script("03_locate.py")


class JavaAstTests(unittest.TestCase):
    def test_line_numbers_match_splitlines_at_every_byte_offset(self):
        samples = [
            b"", b"a\r\nb\rc\nd", b"\x0b\x0c\x1c\x1d\x1e",
            " // 中文 Россия café\n\u0085\u2028\u2029last".encode(),
            b"\xff\x85\xc2\x85\xe2\x80\xa8\xf0\x9f\x98\x80\r\n",
        ]
        for src in samples:
            for offset in range(len(src) + 1):
                node = SimpleNamespace(start_byte=offset, end_byte=offset)
                expected = len((src[:offset].decode("utf-8", "replace") + "x").splitlines())
                with self.subTest(src=src, offset=offset):
                    self.assertEqual(LineIndex(src).line(offset), expected)
                    self.assertEqual(astjava.line1(node, src), expected)
                    self.assertEqual(astjava.line1(node, src, "end"), expected)

    def test_leading_whitespace_unicode_and_nested_methods(self):
        src = ("\n  class A {\n"
               "  /** 中文 */\n"
               "  @Deprecated\n"
               "  void outer() {\n"
               "    // Россия\n"
               "    Object x = new Object() {\n"
               "      void inner() { /* nested */ }\n"
               "    };\n"
               "  }\n"
               "  native void absent();\n"
               "}\n").encode()
        tree = astjava.parse(src)
        methods = astjava.methods(tree)
        self.assertEqual([m["name"] for m in methods], ["outer", "inner", "absent"])
        self.assertEqual((methods[0]["start"], methods[0]["end"]), (4, 10))
        self.assertEqual(astjava.enclosing(tree, 8)["name"], "inner")
        self.assertEqual(astjava.locate_method(tree, 3)["name"], "outer")
        self.assertIsNone(methods[2]["body_start"])
        self.assertEqual(astjava.doc_block(tree, methods[0]),
                         [{"start": 3, "end": 3, "kind": "block", "doc": True}])
        self.assertEqual([i for i, flag in enumerate(astjava.comment_lines(tree)) if flag],
                         [3, 6, 8])
        self.assertEqual(astjava.errors(tree), [])

    def test_random_malformed_utf8_line_indexes(self):
        rng = random.Random(31)
        for _ in range(150):
            source = bytes(rng.randrange(256) for _ in range(rng.randrange(100)))
            index = LineIndex(source)
            for offset in range(len(source) + 1):
                expected = len((source[:offset].decode("utf-8", "replace") + "x").splitlines())
                self.assertEqual(index.line(offset), expected)

    def test_parallel_parses_preserve_sources(self):
        def inspect(i):
            tree = astjava.parse(f"class A{i} {{ void method{i}() {{ // 中文\n }} }}".encode())
            return astjava.methods(tree)[0]["name"]
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(list(executor.map(inspect, range(64))),
                              [f"method{i}" for i in range(64)])

    def test_cached_ast_results_remain_independent(self):
        tree = astjava.parse(b"class A { A() {} void method() { // comment\n } }")
        methods = astjava.methods(tree)
        methods[0]["name"] = "changed"
        self.assertEqual(astjava.methods(tree)[0]["name"], "A")
        comments = astjava.comments(tree)
        comments[0]["text"] = "changed"
        self.assertEqual(astjava.comments(tree)[0]["text"], "// comment")


class CommentMatchingTests(unittest.TestCase):
    def test_characterized_random_cases(self):
        rng = random.Random(7)
        pool = ["// stable text", "// changed text", "// 初始化记录数据", "//}", "",
                "queue.push(); // stable text", "int value;", "/* start", "* end */",
                "// This long comment describes the current state of this object."]
        results = []
        for _ in range(500):
            lines = rng.choices(pool, k=rng.randrange(1, 20))
            cl = rng.choices(pool, k=rng.randrange(1, 4))
            start = rng.randrange(1, len(lines) + 1)
            mask = (None if rng.choice([True, False]) else
                    [False] + [rng.choice([True, False]) for _ in lines])
            used = {rng.randrange(1, len(lines) + 1)}
            results.append(map_comment(cl, lines, start, len(lines), lo=0,
                                       hi=len(lines), is_comment=mask, used=used))
        digest = hashlib.sha256(json.dumps(results).encode()).hexdigest()
        self.assertEqual(digest,
                         "e875b9d5c616c1e221f900c53432d93006039cdef9480776083b22a65d69c2d8")

    def match(self, cl, lines, start=1, **kwargs):
        return locator.map_comment(cl, lines, start, len(lines), lo=0, hi=len(lines), **kwargs)

    def test_empty_and_symbol_only(self):
        self.assertEqual(self.match([], ["// comment"]), ([], None))
        self.assertEqual(self.match(["//}"], ["//}"]), ([], None))

    def test_exact_skips_blank_lines(self):
        self.assertEqual(self.match(["// first", "// second"],
                                    ["// first", "", "// second"]), ([1, 2, 3], "exact"))

    def test_exact_tie_uses_distance_from_function_start(self):
        self.assertEqual(self.match(["// same"], ["// same", "", "// same"], start=3),
                         ([3], "exact"))

    def test_structural_trailing_comment(self):
        self.assertEqual(self.match(["// for thread safety"],
                                    ["queue.push(task); // for thread safety"]), ([1], "structural"))

    def test_structural_preserves_one_line(self):
        self.assertEqual(self.match(["// stable text", "// changed text"],
                                    ["// stable text", "// completely rewritten"]),
                         ([1, 2], "structural"))

    def test_ast_mask_rejects_code(self):
        self.assertEqual(self.match(["// missing"], ["String missing;"],
                                    is_comment=[False, False]), ([], None))

    def test_unicode_exact(self):
        self.assertEqual(self.match(["// 初始化记录数据"], ["// 初始化记录数据"]),
                         ([1], "exact"))

    def test_exact_precedence_is_unchanged_even_for_used_lines(self):
        # Existing behavior is deliberately characterized, not tightened here.
        self.assertEqual(self.match(["// same"], ["// same"], used={1}), ([1], "exact"))


class SourceFilesTests(unittest.TestCase):
    def test_head_cache_is_local_to_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            file = directory / "A.java"
            file.write_text("first\n")
            cache = SourceFiles()
            self.assertEqual(cache.read(directory, "A.java"), ["first"])
            file.write_text("second\n")
            self.assertEqual(cache.read(directory, "A.java"), ["first"])
            self.assertEqual(SourceFiles().read(directory, "A.java"), ["second"])

    def test_revisions_paths_and_eviction(self):
        result = SimpleNamespace(returncode=0, stdout="source\n", stderr="")
        with patch("source_files.git", return_value=result) as git:
            cache = SourceFiles(maxsize=2)
            directory = Path("repo")
            for path, rev in [("A", "one"), ("A", "two"), ("A", "one"),
                              ("B", "one"), ("A", "two")]:
                self.assertEqual(cache.read(directory, path, rev), ["source"])
            self.assertEqual(git.call_count, 4)

    def test_failed_reads_are_not_cached(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="missing")
        with patch("source_files.git", return_value=result) as git:
            cache = SourceFiles()
            for _ in range(2):
                with self.assertRaisesRegex(OSError, "missing"):
                    cache.read(Path("repo"), "A", "rev")
            self.assertEqual(git.call_count, 2)


class VerificationTests(unittest.TestCase):
    def check(self, kind, want, got):
        row = {"comment_line": "1", "n_comment_lines": str(len(got)),
               "commenttext": want, "match": kind}
        with patch("provenance_checks.content", return_value=got):
            return provenance_checks.check_text(row)

    def test_existing_match_policies(self):
        cases = [
            ("exact", "// first\n// second", ["// first", "", "// second"], None),
            ("exact", "// first", ["// second"], "exact but differs"),
            ("structural", "// first", ["run(); // first"], None),
            ("structural", "// first", ["// second"], "structural, no shared line"),
            ("relocated", "// first\n// second", ["// first"], "relocated but differs"),
            ("reshaped", "/* first\n * second */", ["/* first second */"], None),
            ("position", "//}", ["run(); //}"], None),
            ("position", "//}", ["// text"], "position: text present"),
            ("history", "// 中文", ["// 中文"], None),
            ("fuzzy", "// Same comment with enough words for comparison",
             ["// Same comment with enough words for comparison"], None),
        ]
        for kind, want, got, expected in cases:
            with self.subTest(kind=kind, got=got):
                self.assertEqual(self.check(kind, want, got), expected)


class CommitVerificationTests(unittest.TestCase):
    def test_snapshot_path_and_body_check(self):
        row = {"repo": "example/project", "commit_id": "abc123",
               "path": "New.java", "path_at_commit": "Old.java",
               "match": "structural", "commenttext": "// preserved text"}
        sources = SourceFiles()
        with patch.object(sources, "read", return_value=["run(); // preserved text"]) as read:
            self.assertIsNone(provenance_checks.check_commit(row, sources))
            self.assertEqual(read.call_args.args[1:], ("Old.java", "abc123"))

    def test_fuzzy_requires_nonempty_file(self):
        row = {"repo": "example/project", "commit_id": "abc123",
               "path": "New.java", "path_at_commit": "", "match": "fuzzy"}
        sources = SourceFiles()
        with patch.object(sources, "read", return_value=["  ", ""]):
            self.assertEqual(provenance_checks.check_commit(row, sources), "empty file at commit")

    def test_failed_snapshot_is_reported(self):
        row = {"repo": "example/project", "commit_id": "abc123", "path": "New.java",
               "path_at_commit": ""}
        sources = SourceFiles()
        with patch.object(sources, "read", side_effect=OSError("missing")):
            self.assertEqual(provenance_checks.check_commit(row, sources), "show failed: missing")


class GitAuthenticationTests(unittest.TestCase):
    def setUp(self):
        git_auth._cli_token.cache_clear()

    def tearDown(self):
        git_auth._cli_token.cache_clear()

    def test_environment_token_skips_cli_and_is_read_fresh(self):
        with patch.dict("os.environ", {"GH_TOKEN": "test-first"}), \
                patch("git_auth.subprocess.run") as run:
            first = git_auth.git_env()
            with patch.dict("os.environ", {"GH_TOKEN": "test-second"}):
                second = git_auth.git_env()
            self.assertIn("test-first", first["GIT_CONFIG_KEY_0"])
            self.assertIn("test-second", second["GIT_CONFIG_KEY_0"])
            run.assert_not_called()

    def test_parallel_cli_lookup_is_cached_but_environments_are_independent(self):
        result = SimpleNamespace(stdout="test-token\n")
        with patch.dict("os.environ", {"GH_TOKEN": ""}), \
                patch("git_auth.subprocess.run", return_value=result) as run:
            with ThreadPoolExecutor(max_workers=8) as executor:
                environments = list(executor.map(lambda _: git_auth.git_env(), range(32)))
            run.assert_called_once()
            self.assertTrue(all(e["GIT_TERMINAL_PROMPT"] == "0" for e in environments))
            environments[0]["GIT_TERMINAL_PROMPT"] = "changed"
            self.assertEqual(environments[1]["GIT_TERMINAL_PROMPT"], "0")


if __name__ == "__main__":
    unittest.main()
