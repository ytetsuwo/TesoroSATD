"""Check that every mapped comment really sits inside the located function.

A comment is OK when it falls between the function's declaration and its
closing brace (found by brace matching on the actual file, at HEAD or at the
snapshot commit for ok_history rows). Comments of a javadoc block directly
above the declaration are reported separately: they describe the function but
are outside its braces.

    python3 scripts/14_check_span.py            # summary + worst offenders
    python3 scripts/14_check_span.py --json     # one record per function to work/span_check.jsonl
"""
from __future__ import annotations

import json
import re
import sys

from common import OUT, WORK, body_end, git, repo_dir

COMMENT_LINE = re.compile(r"^\s*(//|/\*|\*)")


def strip_code(line: str) -> str:
    """Drop string/char literals and comment text so braces can be counted."""
    line = re.sub(r'"(\\.|[^"\\])*"', '""', line)
    line = re.sub(r"'(\\.|[^'\\])*'", "''", line)
    line = re.sub(r"//.*", "", line)
    return line


def body_end(lines: list[str], start: int) -> int | None:
    """Closing-brace line (1-based) of the function that starts at `start`."""
    depth = 0
    opened = False
    in_block = False
    for i in range(start - 1, len(lines)):
        text = lines[i]
        if in_block:
            if "*/" in text:
                in_block = False
                text = text.split("*/", 1)[1]
            else:
                continue
        text = re.sub(r"/\*.*?\*/", "", text)
        if "/*" in text:
            in_block = True
            text = text.split("/*", 1)[0]
        text = strip_code(text)
        depth += text.count("{") - text.count("}")
        opened = opened or "{" in text
        if opened and depth <= 0:
            return i + 1
    return None


def file_lines(repo: str, path: str, at_head: bool, snapshot: str) -> list[str]:
    d = repo_dir(repo)
    if at_head:
        return (d / path).read_text(errors="replace").splitlines()
    p = git(d, "show", f"{snapshot}:{path}")
    return p.stdout.splitlines() if p.returncode == 0 else []


def main() -> int:
    stats: dict[str, dict[str, int]] = {}
    records = []
    for line in open(OUT / "functions_located.jsonl"):
        f = json.loads(line)
        if f.get("status") not in ("ok", "ok_history") or not f.get("start"):
            continue
        at_head = f.get("at_head", True)
        lines = file_lines(f["repo"], f["path"], at_head,
                           f.get("commit_sha") or f.get("commit") or "")
        if not lines:
            continue
        start = f["start"]
        end = body_end(lines, start) or f.get("end") or start
        rec = {"id": f["id"], "repo": f["repo"], "start": start, "end": end,
               "comments": []}
        for c in f["comments"]:
            if not c.get("lines"):
                continue
            ln = c["lines"][0]
            last = c["lines"][-1]
            if start <= ln and last <= end:
                where = "inside"
            elif last < start:
                # javadoc/line comment block that ends right above the declaration,
                # possibly with blank lines or annotations in between
                between = [x for x in lines[last:start - 1]
                           if x.strip() and not x.strip().startswith("@")
                           and not x.lstrip().startswith(("*", "//", "/*", "*/"))]
                where = "above" if not between else "outside"
            else:
                where = "outside"
            kind = c.get("match") or "?"
            key = stats.setdefault(kind, {"inside": 0, "above": 0, "outside": 0})
            key[where] += 1
            rec["comments"].append({"comment_id": c["comment_id"], "line": ln,
                                    "kind": kind, "where": where})
        records.append(rec)

    for kind, v in sorted(stats.items()):
        print(f"{kind:10} inside {v['inside']:5}  above {v['above']:4}  outside {v['outside']:4}")

    bad = [(r, c) for r in records for c in r["comments"] if c["where"] == "outside"]
    print(f"\noutside the function: {len(bad)} comment(s) in {len({r['id'] for r, _ in bad})} function(s)")
    for r, c in bad[:20]:
        print(f"  {r['id']:6} {c['kind']:10} comment {c['comment_id']:3} line {c['line']:6} "
              f"span {r['start']}-{r['end']}  {r['repo']}")

    if "--json" in sys.argv:
        with open(WORK / "span_check.jsonl", "w") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nwrote {WORK / 'span_check.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
