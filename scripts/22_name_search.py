#!/usr/bin/env python3
"""Locate a weak_match function by its method name, across sibling clones.

A dataset `repo` may point at a fork whose checkout is only an overlay (for
example `kiwibrowser/src.next` for Chromium), so the file that holds the method
is missing there. This pass takes the declaration name from the dataset code,
searches that clone and other clones of the same project, and keeps the best
aligned declaration when the name matches and the ratio is acceptable.

    python3 scripts/22_name_search.py [--dry]
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from difflib import SequenceMatcher

from common import OUT, REPOS, core, norm, repo_dir, similarity

NAME = re.compile(r"([A-Za-z_$][\w$]*)\s*$")
DECL = re.compile(r"^\s*(?:@\w+\s+)*(?:public|protected|private|static|final|abstract|synchronized|native|default|\s)*[\w<>\[\],.?\s]+\b{name}\s*\(")


def decl_name(code: str):
    for line in code.splitlines():
        s = line.strip()
        if not s or s.startswith(("@", "//", "*", "/*", "}")) or "(" not in s or s.endswith(";"):
            continue
        m = NAME.search(s[:s.index("(")])
        if m:
            return m.group(1)
    return None


def siblings(repo: str):
    """Clones whose name shares the project part (e.g. *__chromium)."""
    base = repo.split("/")[-1].lower()
    out = []
    for d in REPOS.iterdir():
        if not d.is_dir():
            continue
        name = d.name.lower()
        if name == repo.replace("/", "__").lower():
            continue
        if base and base in name:
            out.append(d)
    return out[:6]


def best_in(d, name, ref):
    """Best aligned declaration of `name` in the clone (path, start, end, ratio)."""
    g = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                        "grep", "-n", "-F", f"{name}(", "--", "*.java"],
                       capture_output=True, text=True)
    best = None
    seen = set()
    for line in g.stdout.splitlines():
        try:
            path, ln, text = line.split(":", 2)
        except ValueError:
            continue
        if (path, ln) in seen:
            continue
        seen.add((path, ln))
        try:
            lines = (d / path).read_text(errors="replace").splitlines()
        except OSError:
            continue
        i = int(ln) - 1
        # the declaration must start here, not be a call site
        head = " ".join(lines[i:i + 2])
        if name + "(" not in head:
            continue
        span = lines[i:i + max(6, len(ref))]
        ratio = similarity(" ".join(core(x) for x in ref if core(x)),
                           " ".join(core(x) for x in span if core(x)))
        if best is None or ratio > best[3]:
            best = (path, i + 1, i + len(span), ratio)
    return best


def main() -> int:
    dry = "--dry" in sys.argv
    path = OUT / "functions_located.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    code = {str(r["id"]): r for r in (json.loads(l) for l in open(OUT.parent / "tesoro" / "data" / "tesoro" / "tesoro_code.json"))}
    hits = 0
    for r in rows:
        if r.get("status") != "weak_match":
            continue
        f = code.get(str(r["id"]))
        if not f:
            continue
        name = decl_name(f["original_code"])
        if not name:
            continue
        ref = [norm(x) for x in f["original_code"].splitlines()]
        cands = [repo_dir(r["repo"])] + siblings(r["repo"])
        best = None
        for d in cands:
            if not (d / ".git").exists():
                continue
            b = best_in(d, name, ref)
            if b and (best is None or b[3] > best[4]):
                best = (d.name.replace("__", "/", 1), b[0], b[1], b[2], b[3])
        if not best or best[4] < 0.45:
            print(f"MISS {r['id']:>6} {name:28} best={best[4] if best else 0:.2f}")
            continue
        hits += 1
        repo, p, start, end, ratio = best
        print(f"OK   {r['id']:>6} {name:28} {repo:28} {p[-45:]:45} r={ratio:.2f}")
        if not dry:
            r["path"] = p
            r["start"], r["end"] = start, end
            r["ratio"] = round(ratio, 3)
            r["reason"] = f"located by method name in {repo}"
    if not dry:
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows))
        tmp.replace(path)
    print(f"\n{hits} updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
