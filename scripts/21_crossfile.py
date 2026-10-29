#!/usr/bin/env python3
"""Find unmapped comments in another file of the same repository.

Some repositories ship several copies of the same sources (version directories,
vendored copies). The function may be located in one copy while the comment
survived in another, so the comment is still the same one.

For every comment that has no line yet, its longest text line is searched with
`git grep` across the whole clone; a hit is accepted when the whole text is
present in that file (or the similarity is high), and the first line is blamed.

    python3 scripts/21_crossfile.py [--dry]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime

from common import OUT, core, repo_dir, similarity

_COMMENTS = {}
for _r in (json.loads(x) for x in open(OUT.parent / "tesoro" / "data" / "tesoro" / "tesoro_comment.json")):
    _COMMENTS.setdefault(_r["id"], []).append(_r)

MIN = 0.9


def blame(d, path, ln):
    p = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                        "blame", "--line-porcelain", "-L", f"{ln},{ln}", "--", path],
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
    dry = "--dry" in sys.argv
    path = OUT / "functions_located.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    found, tried = [], 0
    for loc in rows:
        if loc.get("status") in ("unavailable",):
            continue
        d = repo_dir(loc["repo"])
        if not (d / ".git").exists():
            continue
        if not loc.get("comments"):
            # weak_match rows carry no comment list; rebuild it from the dataset
            loc["comments"] = [{"comment_id": r["comment_id"], "comment": r["comment"],
                                "classification": r.get("classification"), "lines": []}
                               for r in _COMMENTS.get(loc["id"], [])]
        for c in loc.get("comments") or []:
            if c.get("lines"):
                continue
            text = c.get("comment") or ""
            cl = [core(x) for x in text.splitlines() if core(x)]
            if not cl:
                continue
            tried += 1
            anchor = max((x.strip() for x in text.splitlines() if core(x)), key=len)
            g = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                                "grep", "-l", "-F", anchor, "--", "*.java"],
                               capture_output=True, text=True)
            files = [l for l in g.stdout.splitlines() if l]
            hit = None
            for f in files:
                p = d / f
                try:
                    lines = p.read_text(errors="replace").splitlines()
                except OSError:
                    continue
                i = next((i for i, l in enumerate(lines) if anchor in l), None)
                if i is None:
                    continue
                span = lines[max(0, i - 6):i + len(cl) + 6]
                blob = " ".join(core(x) for x in span)
                ok = all(x in blob for x in cl)
                if not ok:
                    ok = similarity(" ".join(cl), " ".join(core(x) for x in span)) >= MIN
                if ok:
                    # the anchor may sit inside the block, so back up to its start
                    off = next((k for k, x in enumerate(cl) if x and x in core(lines[i])), 0)
                    start = max(0, i - off)
                    while start > 0 and core(lines[start - 1]) and "//" not in lines[start - 1] \
                            and "/*" not in lines[start - 1] and "*" not in lines[start - 1]:
                        break
                    hit = (f, start + 1)
                    break
            if not hit:
                continue
            f, ln = hit
            info = blame(d, f, ln) if not dry else {}
            c["lines"] = list(range(ln, ln + len(cl)))
            c["match"] = "relocated"
            c["commit"] = info.get("sha")
            c["author"] = info.get("author")
            c["author_time"] = info.get("date")
            c["summary"] = info.get("summary")
            c["path_snapshot"] = f
            c["path_at"] = f
            found.append((loc["id"], c["comment_id"], loc["repo"], f, ln, info.get("sha")))
            print(f"OK   {loc['id']:>6} c{c['comment_id']:<3} {loc['repo'][:30]:30} "
                  f"{f[-45:]:45} L{ln:<5} {(info.get('sha') or '')[:10]}")

    if not dry:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
        tmp.replace(path)
    print(f"\nfound {len(found)} of {tried} unmapped comments")
    return 0


if __name__ == "__main__":
    sys.exit(main())
