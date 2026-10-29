#!/usr/bin/env python3
"""Recover comments whose text is no longer in the file at HEAD.

The function was found, but the comment itself was deleted or rewritten later.
`git log -S<first line>` lists the commits that added or removed that line; each
candidate revision (and its parent) is then searched for the block with the same
text, and the line is blamed at the revision where it still exists.

Matches are written back into out/functions_located.jsonl with
`match: "history"`, `at_commit` (the revision the line was found in) and the
blame commit, so 04_export emits them as `at_head=false` rows.

    python3 scripts/17_history_text.py [id ...]
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime

import astjava
from common import OUT, core, norm, repo_dir, similarity

_spec = importlib.util.spec_from_file_location("loc03", OUT.parent / "scripts" / "03_locate.py")
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


def show(d, rev: str, path: str):
    p = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                        "show", f"{rev}:{path}"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def blame(d, rev: str, path: str, ln: int) -> dict:
    p = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                        "blame", "--line-porcelain", "-L", f"{ln},{ln}", rev,
                        "--", path], capture_output=True, text=True)
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


def search(d, path: str, cl, revs):
    for rev in revs:
        src = show(d, rev, path)
        if not src:
            continue
        lines = src.splitlines()
        mask = astjava.comment_lines(astjava.parse(src.encode()))
        lines_abs, kind = L.map_comment(cl, lines, 1, max(1, len(lines)), lo=0,
                                        hi=len(lines), is_comment=mask)
        if kind in ("exact", "structural"):
            want = " ".join(cl)
            got = " ".join(core(lines[i - 1]) for i in lines_abs)
            if similarity(want, got) >= 0.85:
                return rev, lines_abs
    return None, None


def main() -> int:
    path = OUT / "functions_located.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    found, missing = [], []
    for loc in rows:
        if only and loc["id"] not in only:
            continue
        if loc.get("status") not in ("ok", "ok_history"):
            continue
        d = repo_dir(loc["repo"])
        fpath = loc.get("path") or ""
        for c in loc.get("comments") or []:
            if c.get("lines"):
                continue
            text = c.get("comment") or ""
            cl = [norm(x) for x in text.splitlines() if norm(x)]
            if not cl or not any(core(x) for x in cl):
                continue
            revs = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                                   "log", "--all", "--format=%H", "-S", norm(text.splitlines()[0]),
                                   "--", fpath],
                                  capture_output=True, text=True).stdout.split()
            rev, lines_abs = search(d, fpath, cl, revs + [r + "^" for r in revs])
            if rev is None:
                missing.append((loc["id"], c["comment_id"], loc["repo"],
                                text.splitlines()[0][:60]))
                continue
            info = blame(d, rev, fpath, lines_abs[0])
            c["lines"] = lines_abs
            c["match"] = "history"
            c["commit"] = info.get("sha")
            c["author"] = info.get("author")
            c["author_time"] = info.get("date")
            c["summary"] = info.get("summary")
            c["at_commit"] = rev
            c["path_at"] = L.path_at_commit(d, fpath, info.get("sha"))
            found.append((loc["id"], c["comment_id"], loc["repo"], lines_abs[0], rev,
                          info.get("sha"), info.get("date"), info.get("summary")))

    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)

    for f in found:
        print(f"OK   {f[0]:>6} c{f[1]:<3} {f[2][:32]:32} L{f[3]:<5} {f[4][:9]} -> "
              f"{(f[5] or '')[:10]} {f[6]} {(f[7] or '')[:45]}")
    for m in missing:
        print(f"MISS {m[0]:>6} c{m[1]:<3} {m[2][:32]:32} {m[3]!r}")
    print(f"\n{len(found)} resolved, {len(missing)} not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
