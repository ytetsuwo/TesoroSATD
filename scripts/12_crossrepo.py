#!/usr/bin/env python3
"""Recover `repo_missing` functions from other clones (upstream / forks).

The original repository is gone from GitHub, but the same code often survives
in a fork or in the upstream project. This script

  1. collects the longest code lines of every unresolved function,
  2. scans all clones in repos/ once with `rg -F -f` for those lines,
  3. aligns the function window in each hit and keeps the best one (ratio>=0.7),
  4. blames the comments there and rewrites out/functions_located.jsonl with
     status "ok_elsewhere" (the row keeps `repo` = where the code was found and
     `original_repo` = the deleted repository).

Usage: python3 scripts/12_crossrepo.py [function_id ...]
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

from common import OUT, REPOS, WORK, load_tesoro, norm, repo_dir


def read_lines(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("loc03", HERE / "03_locate.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

MIN_RATIO = 0.7


def refs_for(fid, funcs, comments):
    rep = max(comments.get(fid, []), key=lambda r: len(r.get("code") or ""), default=None)
    code = (rep or {}).get("code") or funcs[fid].get("original_code") or ""
    ref = [norm(x) for x in code.splitlines()]
    return ref, [x for x in ref if x]


def global_hits(patterns: list) -> list:
    """One rg pass over every clone; returns (repo, path, line, text)."""
    pf = WORK / "crossrepo_patterns.txt"
    pf.write_text("".join(p + "\n" for p in patterns))
    p = subprocess.run(["rg", "--no-ignore-vcs", "--no-heading", "-n", "-F",
                        "-f", str(pf), "-g", "*.java", "."],
                       cwd=str(REPOS), capture_output=True, text=True,
                       errors="replace", timeout=7200)
    hits = []
    for line in p.stdout.splitlines():
        parts = line.split(":", 3)
        if len(parts) != 4 or not parts[2].isdigit():
            continue
        repo = parts[0].lstrip("./").replace("__", "/", 1)
        hits.append((repo, parts[1].lstrip("./"), int(parts[2]), norm(parts[3])))
    return hits


def best_window(repo, ref, cands):
    d = repo_dir(repo)
    cache = {}
    best = None
    for path, start in cands:
        lines = cache.get(path)
        if lines is None:
            lines = cache[path] = read_lines(d / path)
        if not lines:
            continue
        win = [norm(x) for x in lines[start - 1:start - 1 + len(ref)]]
        win_ne = [x for x in win if x]
        if not win_ne:
            continue
        ratio = SequenceMatcher(None, ref, win_ne, autojunk=False).ratio()
        if best is None or ratio > best[0]:
            best = (ratio, path, start, lines)
    return best


def locate_elsewhere(rec, funcs, comments, hits_by_text):
    fid = rec["id"]
    ref, ref_ne = refs_for(fid, funcs, comments)
    if not ref_ne:
        return None
    index_of = {}
    for i, t in enumerate(ref):
        index_of.setdefault(t, []).append(i)

    # candidate start offsets, seeded by hits of the longest line that survived
    cands = {}
    for text in sorted(ref_ne, key=len, reverse=True):
        for repo, path, ln, _ in hits_by_text.get(text, []):
            for idx in index_of.get(text, [])[:2]:
                if ln - idx >= 1:
                    cands.setdefault(repo, set()).add((path, ln - idx))
    if not cands:
        return None

    best = None
    for repo, starts in cands.items():
        got = best_window(repo, ref_ne, sorted(starts)[:200])
        if got and (best is None or got[0] > best[0][0]):
            best = (got, repo)
    if best is None:
        return None
    (ratio, path, start, lines), repo = best
    if ratio < MIN_RATIO:
        return None

    d = repo_dir(repo)
    out_comments = []
    for r in comments.get(fid, []):
        cl = [norm(x) for x in (r.get("comment") or "").splitlines() if norm(x)]
        lines_abs, kind = L.map_comment(cl, lines, start, start + len(ref) - 1)
        if not lines_abs:
            out_comments.append({"comment_id": r.get("comment_id"),
                                 "comment": r.get("comment"),
                                 "classification": r.get("classification"),
                                 "lines": []})
            continue
        blames = [L.blame(d, path, ln, ln) for ln in lines_abs[:12]]
        first = blames[0] if blames else {}
        out_comments.append({
            "comment_id": r.get("comment_id"),
            "comment": r.get("comment"),
            "classification": r.get("classification"),
            "lines": lines_abs,
            "match": kind,
            "commit": first.get("sha"),
            "author": first.get("author"),
            "author_time": first.get("author_time"),
            "summary": first.get("summary"),
            "path_at": first.get("path"),
            "shas": [b.get("sha") for b in blames],
        })

    mapped = [c for c in out_comments if c["lines"]]
    res = {"id": fid, "repo": repo, "original_repo": rec["repo"],
           "label": rec.get("label"), "n_comments": len(comments.get(fid, [])),
           "status": "ok_elsewhere", "path": path, "start": start,
           "end": start + len(ref) - 1, "ratio": round(ratio, 3),
           "head": L.head_of(d),
           "confidence": "high" if ratio >= 0.9 else "medium",
           "comments": out_comments, "n_mapped": len(mapped)}
    if mapped and mapped[0].get("commit"):
        res["commit"] = mapped[0]["commit"]
        res["path_at_commit"] = mapped[0].get("path_at") or path
    return res


def main():
    funcs, comments = load_tesoro()
    path = OUT / "functions_located.jsonl"
    rows = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r

    todo = [r for r in rows.values() if r["status"] == "repo_missing"]
    if len(sys.argv) > 1:
        want = {int(a) for a in sys.argv[1:]}
        todo = [r for r in todo if r["id"] in want]
    print(f"{len(todo)} unresolved functions", flush=True)

    patterns = set()
    for r in todo:
        _, ref_ne = refs_for(r["id"], funcs, comments)
        patterns.update(L.anchors_from(ref_ne))
        if len(ref_ne) >= 3:
            patterns.update(sorted(ref_ne, key=len, reverse=True)[:3])
    print(f"{len(patterns)} patterns, scanning {REPOS} ...", flush=True)

    hits_by_text = {}
    for repo, p, ln, text in global_hits(sorted(patterns)):
        hits_by_text.setdefault(text, []).append((repo, p, ln, text))
    print(f"{sum(len(v) for v in hits_by_text.values())} hits in "
          f"{len(hits_by_text)} patterns", flush=True)

    found = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(locate_elsewhere, r, funcs, comments, hits_by_text): r["id"]
                for r in todo}
        for n, fut in enumerate(as_completed(futs), 1):
            fid = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[{n}/{len(todo)}] error {fid}: {e}", flush=True)
                continue
            if not rec:
                print(f"[{n}/{len(todo)}] -            {fid}", flush=True)
                continue
            print(f"[{n}/{len(todo)}] {rec['ratio']:<6} {rows[fid]['repo']} -> "
                  f"{rec['repo']} {rec['path']}:{rec['start']}", flush=True)
            rows[fid] = rec
            found += 1
            tmp = path.with_suffix(".jsonl.tmp")
            tmp.write_text("".join(json.dumps(rows[k], ensure_ascii=False) + "\n"
                                   for k in sorted(rows)))
            tmp.replace(path)
    print(f"\nrecovered {found} functions from other repositories")


if __name__ == "__main__":
    main()
