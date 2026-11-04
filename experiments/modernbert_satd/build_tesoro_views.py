#!/usr/bin/env python3
"""Build the text / commit-log / code views for the Tesoro comments.

The text model uses ``tesoro_comment.json`` directly.  For the log view we take
the last writer recorded in ``out/tesoro_provenance.csv`` and read that
commit's message from the local clone.  The code view is the 10-line context
with the comment lines removed, so the code model cannot read the label from
the comment itself.

    python experiments/modernbert_satd/build_tesoro_views.py \
        --output experiments/modernbert_satd/data/tesoro_views.csv.gz
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def norm_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def clean_code(code: str, comment: str) -> str:
    """Remove the comment from the code context, line by line."""
    if comment and comment in code:
        code = code.replace(comment, "")
    comment_lines = {norm_line(line) for line in comment.splitlines() if norm_line(line)}
    if not comment_lines:
        return code
    return "\n".join(line for line in code.splitlines()
                     if norm_line(line) not in comment_lines)


def commit_message(repo: str, sha: str, cache: dict[tuple[str, str], str]) -> str:
    if not sha:
        return ""
    key = (repo, sha)
    if key in cache:
        return cache[key]
    directory = ROOT / "repos" / repo.replace("/", "__", 1)
    if not (directory / ".git").exists():
        cache[key] = ""
        return ""
    result = subprocess.run(
        ["git", "-C", str(directory), "show", "-s", "--format=%B", sha],
        capture_output=True, text=True, errors="replace",
    )
    message = result.stdout.strip() if result.returncode == 0 else ""
    cache[key] = message
    return message


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--comments", type=Path,
        default=ROOT / "tesoro/data/tesoro/tesoro_comment.json",
    )
    parser.add_argument(
        "--provenance", type=Path,
        default=ROOT / "out/tesoro_provenance.csv",
    )
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / "experiments/modernbert_satd/data/tesoro_views.csv.gz",
    )
    args = parser.parse_args()

    comments = [json.loads(line) for line in args.comments.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    with args.provenance.open("r", encoding="utf-8", newline="") as handle:
        provenance = {
            (row["function_id"], row["comment_id"]): row
            for row in csv.DictReader(handle)
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    cache: dict[tuple[str, str], str] = {}
    missing_commit = 0
    with gzip.open(args.output, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "input_row", "function_id", "comment_id", "project_name",
            "comment_text", "commit_log", "code", "satd", "classification",
        ])
        writer.writeheader()
        for index, row in enumerate(comments):
            key = (str(row["id"]), str(row["comment_id"]))
            record = provenance.get(key, {})
            repo = record.get("repo") or record.get("projectname", "")
            sha = record.get("commit_id", "")
            if not sha:
                missing_commit += 1
            comment = row.get("comment", "")
            context = row.get("code_context_10") or row.get("code_context_2", "")
            writer.writerow({
                "input_row": index,
                "function_id": row["id"],
                "comment_id": row["comment_id"],
                "project_name": repo,
                "comment_text": comment,
                "commit_log": commit_message(repo, sha, cache),
                "code": clean_code(context, comment),
                "satd": 0 if row.get("classification") == "NONSATD" else 1,
                "classification": row.get("classification", ""),
            })
            if (index + 1) % 500 == 0:
                print(f"{index + 1}/{len(comments)}", flush=True)

    print(f"wrote {args.output}; rows {len(comments)}; "
          f"missing commit {missing_commit}; unique commits {len(cache)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
