#!/usr/bin/env python3
"""Find the commit that wrote a marker-only comment (`//}`, `// ----`, `//`).

Such a comment has no words, so `map_comment` can only place it when the line is
still there. When it is gone, the position is recovered from the dataset's own
context (`code_context_2`): the code line the marker sat on, or the declaration
it preceded. The file's history is then walked from the newest commit backwards
until that context line carries the same marker, and `git blame` on it gives the
commit that wrote the comment.

Matched comments are written back into out/functions_located.jsonl with
`match: "position"`, `at_commit` (the revision the line was found in) and the
blame commit, so 04_export emits them as `at_head=false` rows.

    python3 scripts/16_symbol_history.py [id ...]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime

import importlib.util
from common import OUT, WORK, WS, core, repo_dir, strip_marker

_spec = importlib.util.spec_from_file_location("loc03", OUT.parent / "scripts" / "03_locate.py")
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

TESORO = OUT.parent / "tesoro" / "data" / "tesoro" / "tesoro_comment.json"
MARKS = ("//", "/*")


def sym(s: str) -> str:
    return WS.sub("", strip_marker(s))


def show(d, rev: str, path: str):
    p = subprocess.run(["git", "-C", str(d), "show", f"{rev}:{path}"],
                       capture_output=True, text=True)
    return p.stdout.splitlines() if p.returncode == 0 else None


def blame(d, rev: str, path: str, ln: int) -> dict:
    p = subprocess.run(["git", "-C", str(d), "blame", "--line-porcelain",
                        "-L", f"{ln},{ln}", rev, "--", path],
                       capture_output=True, text=True)
    out = {}
    for line in p.stdout.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            out["sha"] = line.split()[0]
        elif line.startswith("author "):
            out["author"] = line[7:]
        elif line.startswith("author-time "):
            out["date"] = datetime.fromtimestamp(int(line[12:])).strftime("%Y-%m-%d")
        elif line.startswith("summary "):
            out["summary"] = line[8:]
    return out


def marker_on(line: str, target: str):
    """Index just after the marker whose body equals `target`, or None."""
    for mark in MARKS:
        i = line.find(mark)
        if i >= 0 and sym(line[i:]) == target:
            return i
    return None


def placement(ctx: str, text: str):
    """(code before the marker, previous line) for a marker-only comment."""
    target = sym(text.splitlines()[0])
    lines = ctx.splitlines()
    for j, line in enumerate(lines):
        i = marker_on(line, target)
        if i is None:
            continue
        before = core(line[:i])
        prev = core(lines[j - 1]) if not before and j else ""
        return before, prev
    return None, None


def find_line(d, path: str, text: str, ctx: str):
    """(rev, line) where this marker-only comment last existed."""
    target = sym(text.splitlines()[0])
    before, prev = placement(ctx, text)
    revs = subprocess.run(["git", "-C", str(d), "log", "--all", "--format=%H", "--", path],
                          capture_output=True, text=True).stdout.split()
    for rev in revs:
        lines = show(d, rev, path)
        if not lines:
            continue
        for k, line in enumerate(lines):
            if before and before not in core(line):
                continue
            i = marker_on(line, target)
            if i is None:
                continue
            if before or (prev and k and prev in core(lines[k - 1])):
                return rev, k + 1
    return None, None


def find_empty_javadoc(d, path: str, ctx: str):
    """Empty `/** */` directly above the first code line of the context."""
    anchor = core(ctx.splitlines()[0]) if ctx.strip() else ""
    if not anchor:
        return None, None
    revs = subprocess.run(["git", "-C", str(d), "log", "--all", "--format=%H", "--", path],
                          capture_output=True, text=True).stdout.split()
    for rev in revs:
        lines = show(d, rev, path)
        if not lines:
            continue
        for k, line in enumerate(lines):
            if anchor not in core(line) or k < 2:
                continue
            if lines[k - 1].strip() == "*/" and lines[k - 2].strip() == "/**":
                return rev, k - 1
    return None, None


def main() -> int:
    _, comments = None, {}
    for r in (json.loads(x) for x in open(TESORO)):
        comments.setdefault(r["id"], []).append(r)

    path = OUT / "functions_located.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    found, missing = [], []
    for loc in rows:
        if only and loc["id"] not in only:
            continue
        by_cid = {c["comment_id"]: c for c in comments.get(loc["id"], [])}
        for c in loc.get("comments") or []:
            if c.get("lines"):
                continue
            text = c.get("comment") or ""
            if any(core(x) for x in text.splitlines()):
                continue
            rec = by_cid.get(c["comment_id"])
            if not rec:
                continue
            d = repo_dir(loc["repo"])
            ctx = rec.get("code_context_2") or ""
            rev, ln = find_line(d, loc["path"], text, ctx)
            if rev is None and text.startswith("/**"):
                rev, ln = find_empty_javadoc(d, loc["path"], ctx)
            if rev is None:
                missing.append((loc["id"], c["comment_id"], loc["repo"], text))
                continue
            info = blame(d, rev, loc["path"], ln)
            c["lines"] = [ln]
            c["match"] = "position"
            c["commit"] = info.get("sha")
            c["author"] = info.get("author")
            c["author_time"] = info.get("date")
            c["summary"] = info.get("summary")
            c["path_at"] = L.path_at_commit(d, loc["path"], info.get("sha"))
            c["at_commit"] = rev
            found.append((loc["id"], c["comment_id"], loc["repo"], ln, rev,
                          info.get("sha"), info.get("date"), info.get("summary")))

    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)

    for f in found:
        print(f"OK   {f[0]:>6} c{f[1]:<3} {f[2][:34]:34} L{f[3]:<5} {f[4][:9]} -> "
              f"{(f[5] or '')[:10]} {f[6]} {(f[7] or '')[:45]}")
    for m in missing:
        print(f"MISS {m[0]:>6} c{m[1]:<3} {m[2][:34]:34} {m[3]!r}")
    print(f"\n{len(found)} resolved, {len(missing)} not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
