#!/usr/bin/env python3
"""Deeper history search for comments that are gone from HEAD.

`17_history_text.py` looks for the exact block at revisions found by `-S` on the
first line and only along the current path. This script widens that:

  * several anchor lines per comment, so a rewritten first line still matches,
  * `-S` without a pathspec, so renamed or moved files are found too,
  * a fuzzy window comparison, so lightly edited wording is accepted.

    python3 scripts/19_deep_history.py [id ...]
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime
from difflib import SequenceMatcher

import astjava
from common import OUT, core, norm, repo_dir, similarity

_spec = importlib.util.spec_from_file_location("loc03", OUT.parent / "scripts" / "03_locate.py")
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

HEADER = re.compile(r"^C ([0-9a-f]{40})$")
MIN = 0.85
MAX_REVS = 40


def show(d, rev, path):
    p = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                        "show", f"{rev}:{path}"], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else None


def blame(d, rev, path, ln):
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


def candidates(d, anchors):
    """(sha, path) pairs from commits that touched any anchor line."""
    seen, out = set(), []
    for a in anchors:
        p = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                            "log", "--all", "--name-only", "--format=C %H",
                            "-S", a], capture_output=True, text=True)
        sha = None
        for line in p.stdout.splitlines():
            m = HEADER.match(line)
            if m:
                sha = m.group(1)
                continue
            path = line.strip()
            if not sha or not path.endswith(".java"):
                continue
            for key in ((sha, path), (sha + "^", path)):
                if key not in seen:
                    seen.add(key)
                    out.append(key)
                    if len(out) >= MAX_REVS:
                        return out
    return out


def best_window(cl, lines):
    """(ratio, start, end) of the closest block of code or comment lines."""
    want = " ".join(cl)
    n = len(cl)
    best = (0.0, 0, 0)
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        if not any(m in line for m in ("//", "/*", "*")):
            continue
        for span in range(max(1, n - 1), n + 4):
            got = " ".join(core(x) for x in lines[i:i + span] if core(x))
            if not got:
                continue
            r = similarity(want, got)
            if r > best[0]:
                best = (r, i + 1, i + span)
    return best


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
        for c in loc.get("comments") or []:
            if c.get("lines"):
                continue
            text = c.get("comment") or ""
            cl = [core(x) for x in text.splitlines() if core(x)]
            if not cl:
                continue
            anchors = sorted({norm(x) for x in text.splitlines() if norm(x)},
                             key=len, reverse=True)[:3]
            hit = None
            for rev, fpath in candidates(d, anchors):
                src = show(d, rev, fpath)
                if not src:
                    continue
                r, a, b = best_window(cl, src.splitlines())
                if r >= MIN:
                    hit = (rev, fpath, a, b, r)
                    break
            if not hit:
                missing.append((loc["id"], c["comment_id"], loc["repo"],
                                text.splitlines()[0][:55]))
                continue
            rev, fpath, a, b, r = hit
            info = blame(d, rev, fpath, a)
            c["lines"] = list(range(a, b + 1))
            c["match"] = "history"
            c["commit"] = info.get("sha")
            c["author"] = info.get("author")
            c["author_time"] = info.get("date")
            c["summary"] = info.get("summary")
            c["at_commit"] = rev
            c["path_at"] = L.path_at_commit(d, fpath, info.get("sha"))
            c["path_snapshot"] = fpath
            found.append((loc["id"], c["comment_id"], loc["repo"], fpath, a, rev,
                          info.get("sha"), info.get("date"), round(r, 2)))

    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)
    for f in found:
        print(f"OK   {f[0]:>6} c{f[1]:<3} {f[2][:30]:30} {f[3].split('/')[-1][:24]:24} "
              f"L{f[4]:<5} {f[5][:9]} -> {(f[6] or '')[:10]} {f[7]} r={f[8]}")
    for m in missing:
        print(f"MISS {m[0]:>6} c{m[1]:<3} {m[2][:30]:30} {m[3]!r}")
    print(f"\n{len(found)} resolved, {len(missing)} not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
