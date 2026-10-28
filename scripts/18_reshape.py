#!/usr/bin/env python3
"""Locate comments whose text survives at HEAD but whose block shape changed.

Tesoro sometimes stores a comment together with the code that follows it, or
with a separator line above it, so the block no longer matches line for line.
Here the first line of the dataset comment is found in the file and the whole
text is required to appear in the following lines; the span is then blamed.

    python3 scripts/18_reshape.py [id ...]
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import datetime

import astjava
from common import OUT, core, norm, repo_dir

_spec = importlib.util.spec_from_file_location("loc03", OUT.parent / "scripts" / "03_locate.py")
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)


def blame(d, rev, path, ln):
    p = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                        "blame", "--line-porcelain", "-L", f"{ln},{ln}"] +
                       ([rev] if rev else []) + ["--", path],
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


def main() -> int:
    path = OUT / "functions_located.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    only = {int(a) for a in sys.argv[1:] if a.isdigit()}
    found, missing = [], []
    for loc in rows:
        if only and loc["id"] not in only:
            continue
        fpath = loc.get("path")
        if not fpath or loc.get("status") not in ("ok", "ok_history"):
            continue
        try:
            lines = (repo_dir(loc["repo"]) / fpath).read_text(errors="replace").splitlines()
        except OSError:
            continue
        mask = astjava.comment_lines(astjava.parse("\n".join(lines).encode()))
        for c in loc.get("comments") or []:
            if c.get("lines"):
                continue
            text = c.get("comment") or ""
            cl = [core(x) for x in text.splitlines() if core(x)]
            if not cl or cl[0] not in " ".join(core(x) for x in lines):
                continue
            hits = []
            for i, line in enumerate(lines):
                if cl[0] not in core(line):
                    continue
                if not (mask[i + 1] or any(m in line for m in ("//", "/*"))):
                    continue
                span = lines[i:i + len(cl) + 6]
                blob = " ".join(core(x) for x in span)
                if all(x in blob for x in cl):
                    hits.append((i, len(cl)))
            if len(hits) != 1:
                missing.append((loc["id"], c["comment_id"], loc["repo"],
                                text.splitlines()[0][:55], len(hits)))
                continue
            i, n = hits[0]
            info = blame(repo_dir(loc["repo"]), None, fpath, i + 1)
            c["lines"] = list(range(i + 1, i + n + 1))
            c["match"] = "reshaped"
            c["commit"] = info.get("sha")
            c["author"] = info.get("author")
            c["author_time"] = info.get("date")
            c["summary"] = info.get("summary")
            c["path_at"] = L.path_at_commit(repo_dir(loc["repo"]), fpath, info.get("sha"))
            found.append((loc["id"], c["comment_id"], loc["repo"], i + 1, n,
                          info.get("sha"), info.get("date")))

    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)

    for f in found:
        print(f"OK   {f[0]:>6} c{f[1]:<3} {f[2][:32]:32} L{f[3]:<5} n={f[4]:<2} "
              f"{(f[5] or '')[:10]} {f[6]}")
    for m in missing:
        print(f"MISS {m[0]:>6} c{m[1]:<3} {m[2][:32]:32} hits={m[4]} {m[3]!r}")
    print(f"\n{len(found)} resolved, {len(missing)} not found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
