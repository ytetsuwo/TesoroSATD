#!/usr/bin/env python3
"""Search the full history for functions that are no longer at HEAD.

For every function whose status is `not_at_head` or `weak_match`:

  1. `git log -S<anchor> -p -- '*.java'` finds the commits that added or removed
     a distinctive line, together with the file it happened in,
  2. the function is aligned against that file at that commit (and at its parent),
  3. the winning version is blamed line by line.

Hits are written back into out/functions_located.jsonl with status `ok_history`
(`at_head: false`) and the commit / path *as of that commit*.

Requires blobs in the clone: run scripts/07_backfill.py first.
Usage: python3 scripts/08_history.py [id ...]
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

from common import OUT, norm, repo_dir, run

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("loc03", HERE / "03_locate.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)

HEADER = re.compile(r"^C ([0-9a-f]{40})$")
MAX_CANDIDATES = 40


def search(repo: str, func: dict, comments: list) -> dict:
    d = repo_dir(repo)
    base = {"id": func["id"], "repo": repo, "label": func.get("label"),
            "n_comments": len(comments), "prev_status": None}
    if not (d / ".git").exists():
        return {**base, "status": "repo_missing"}

    rep = max(comments, key=lambda r: len(r.get("code") or "")) if comments else None
    code = (rep or {}).get("code") or func.get("original_code") or ""
    ref = [norm(x) for x in code.splitlines()]
    ref_ne = [x for x in ref if x]
    if not ref_ne:
        return {**base, "status": "no_anchor"}
    index_of = {}
    for i, t in enumerate(ref):
        index_of.setdefault(t, []).append(i)

    anchors = L.anchors_from(ref_ne) or sorted(ref_ne, key=len, reverse=True)[:3]
    cands, seen = [], set()
    for group in (anchors[:3], ref_ne[:3], L.anchors_from(ref_ne, min_len=12)[:3]):
        cands = collect(d, group, ref_ne, seen)
        if cands:
            anchors = list(dict.fromkeys(group + anchors))
            break
    if not cands:
        return {**base, "status": "not_at_head"}

    # --- pick the commit/path whose content best matches the dataset function
    best = None
    cache = {}
    for sha, path in cands[:MAX_CANDIDATES]:
        lines = cache.get((sha, path))
        if lines is None:
            p = run(["git", "show", f"{sha}:{path}"], d, timeout=600)
            lines = cache[(sha, path)] = (p.stdout.splitlines() if p.returncode == 0 else [])
        if not lines:
            continue
        for a in anchors[:4]:
            for i, text in enumerate(lines):
                if text.strip() != a:
                    continue
                for j in index_of.get(a, [])[:2]:
                    start = i - j + 1
                    if start < 1:
                        continue
                    win = [norm(x) for x in lines[start - 1:start - 1 + len(ref)]]
                    win_ne = [x for x in win if x]
                    if not win_ne:
                        continue
                    ratio = SequenceMatcher(None, ref_ne, win_ne, autojunk=False).ratio()
                    if best is None or ratio > best[0]:
                        best = (ratio, sha, path, start, lines)
    if best is None or best[0] < 0.45:
        return {**base, "status": "not_at_head",
                "ratio": round(best[0], 3) if best else None}

    ratio, sha, path, start, lines = best
    res = {**base, "status": "ok_history", "at_head": False, "path": path,
           "start": start, "end": start + len(ref) - 1, "ratio": round(ratio, 3),
           "commit_sha": sha,
           "confidence": "high" if ratio >= 0.9 else "medium" if ratio >= 0.7 else "low"}

    out_comments = []
    for r in comments:
        cl = [norm(x) for x in (r.get("comment") or "").splitlines() if norm(x)]
        lines_abs, kind = L.map_comment(cl, lines, start, start + len(ref) - 1)
        if not lines_abs:
            out_comments.append({"comment_id": r.get("comment_id"),
                                 "comment": r.get("comment"),
                                 "classification": r.get("classification"),
                                 "lines": []})
            continue
        blames = [blame_at(d, sha, path, ln, ln) for ln in lines_abs[:12]]
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
    res["comments"] = out_comments
    mapped = [c for c in out_comments if c["lines"]]
    res["n_mapped"] = len(mapped)
    if mapped and mapped[0].get("commit"):
        res["commit"] = mapped[0]["commit"]
    res["path_at_commit"] = path
    return res


def collect(d: Path, anchor_group, ref_ne, seen):
    """Commits (and files) where one of the anchor lines was added or removed."""
    cands = []
    for a in anchor_group:
        if not a:
            continue
        p = run(["git", "log", "--all", "--format=C %H", f"-S{a}", "-p", "--", "*.java"],
                d, timeout=1800)
        if p.returncode != 0:
            continue
        sha = plus = minus = None
        for line in p.stdout.splitlines():
            m = HEADER.match(line)
            if m:
                sha, plus, minus = m.group(1), None, None
                continue
            if line.startswith("+++ b/"):
                plus = line[6:]
            elif line.startswith("--- a/"):
                minus = line[6:]
            elif sha and len(line) > 1 and line[0] in "+-" and a in line:
                path = plus if line[0] == "+" else minus
                if path and path.endswith(".java"):
                    for key in ((sha, path), (parent(d, sha), path)):
                        if key[0] and key not in seen:
                            seen.add(key)
                            cands.append(key)
        if len(cands) > MAX_CANDIDATES:
            break
    return cands


def parent(d: Path, sha: str):
    p = run(["git", "rev-parse", f"{sha}^"], d, timeout=60)
    return p.stdout.strip() if p.returncode == 0 else None


def blame_at(d: Path, sha: str, rel: str, start: int, end: int):
    p = run(["git", "blame", "--line-porcelain", "-L", f"{start},{end}", sha, "--", rel],
            d, timeout=600)
    if p.returncode != 0:
        return {}
    out = {"sha": None, "author": None, "author_time": None, "summary": None,
           "path": None}
    for line in p.stdout.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            out["sha"] = line.split()[0]
        elif line.startswith("author "):
            out["author"] = line[7:]
        elif line.startswith("author-time "):
            out["author_time"] = int(line[12:])
        elif line.startswith("summary "):
            out["summary"] = line[8:]
        elif line.startswith("filename "):
            out["path"] = line[9:]
        elif line.startswith("\t"):
            break
    return out


def main():
    from common import load_tesoro
    funcs, comments = load_tesoro()
    path = OUT / "functions_located.jsonl"
    rows = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r

    want = [i for i, r in rows.items() if r["status"] in ("not_at_head", "weak_match")]
    if len(sys.argv) > 1:
        want = [int(a) for a in sys.argv[1:]]
    print(f"{len(want)} functions to search", flush=True)

    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(search, rows[i]["repo"], funcs[i], comments.get(i, [])): i
                for i in want}
        for n, fut in enumerate(as_completed(futs), 1):
            i = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                print(f"[{n}/{len(want)}] error {i}: {e}", flush=True)
                continue
            rec["prev_status"] = rows[i]["status"]
            print(f"[{n}/{len(want)}] {rec['status']:12s} {rec.get('ratio','')} "
                  f"{rec['repo']} {rec.get('path', '')}", flush=True)
            if rec["status"] == "ok_history":
                rows[i] = rec
                tmp = path.with_suffix(".jsonl.tmp")
                tmp.write_text("".join(json.dumps(rows[k], ensure_ascii=False) + "\n"
                                       for k in sorted(rows)))
                tmp.replace(path)
    found = sum(1 for r in rows.values() if r.get("status") == "ok_history")
    print(f"\nrecovered {found} functions from history")


if __name__ == "__main__":
    main()
