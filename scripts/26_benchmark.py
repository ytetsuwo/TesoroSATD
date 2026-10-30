#!/usr/bin/env python3
"""Measure AST inspection and comment matching without changing pipeline output.

    python3 scripts/26_benchmark.py --output work/tmp/benchmark.json

The fixed sample includes large methods, Unicode comments and malformed Java.
Compare checksums as well as timings; this is not an end-to-end benchmark.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import statistics
import time
from pathlib import Path

import astjava
from common import OUT, git, git_env, load_tesoro, norm, read_jsonl, repo_dir

SAMPLE_IDS = (1134, 12982, 18082, 24804, 34583)


def load_locator():
    spec = importlib.util.spec_from_file_location("loc03", Path(__file__).with_name("03_locate.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def measure(operation, repeat):
    durations = []
    for _ in range(repeat):
        start = time.perf_counter()
        result = operation()
        durations.append(time.perf_counter() - start)
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True).encode()
    return {"median_seconds": statistics.median(durations),
            "seconds": durations, "sha256": hashlib.sha256(payload).hexdigest()}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()
    if args.repeat < 1:
        ap.error("--repeat must be positive")
    locator = load_locator()
    _, comments = load_tesoro()
    functions = {r["id"]: r for r in read_jsonl(OUT / "functions_located.jsonl")}
    sample = []
    for fid in SAMPLE_IDS:
        f = functions[fid]
        d = repo_dir(f["repo"])
        if f.get("at_head", True):
            src = (d / f["path"]).read_bytes()
        else:
            src = git(d, "show", f"{f['commit_sha']}:{f['path']}", check=True).stdout.encode()
        sample.append((fid, f, src))

    def inspect_ast():
        result = []
        for fid, f, src in sample:
            tree = astjava.parse(src)
            method = astjava.locate_method(tree, f["start"])
            result.append((fid, astjava.methods(tree), astjava.comment_lines(tree),
                           astjava.doc_block(tree, method) if method else [], astjava.errors(tree)))
        return result

    # Prepare ASTs outside the matching timer so its cost is measured separately.
    prepared = [(fid, f, src.decode("utf-8", "replace").splitlines(),
                 astjava.comment_lines(astjava.parse(src))) for fid, f, src in sample]

    def match_comments():
        result = []
        for fid, f, lines, mask in prepared:
            used = set()
            options = {"index": locator.CommentIndex(lines)} if hasattr(locator, "CommentIndex") else {}
            for c in comments[fid]:
                cl = [norm(x) for x in c["comment"].splitlines() if norm(x)]
                mapped, kind = locator.map_comment(cl, lines, f["start"], f["end"],
                                                  lo=0, hi=len(lines), used=used, is_comment=mask,
                                                  **options)
                used.update(mapped)
                result.append((fid, c["comment_id"], mapped, kind))
        return result

    # Never serialize the authentication environment or token.
    def authentication():
        for _ in range(10):
            git_env()
        return "10 environment preparations"

    report = {"sample": [{"id": fid, "bytes": len(src),
                           "source_sha256": hashlib.sha256(src).hexdigest()}
                          for fid, _, src in sample],
              "ast": measure(inspect_ast, args.repeat),
              "matching": measure(match_comments, args.repeat),
              "authentication": measure(authentication, args.repeat)}
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text, end="")


if __name__ == "__main__":
    main()
