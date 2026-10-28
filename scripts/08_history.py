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

import astjava
from common import OUT, core, norm, repo_dir, run

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
    # Same span rule as HEAD matches: the dataset snippet may be a fragment of
    # a longer method, so the enclosing declaration is the real function.
    tree = astjava.parse("\n".join(lines).encode())
    method = astjava.locate_method(tree, start)
    if method and method["start"] < start:
        start = method["start"]
    end = start + len(ref) - 1
    if method:
        end = max(end, method["end"])

    res = {**base, "status": "ok_history", "at_head": False, "path": path,
           "start": start, "end": end, "ratio": round(ratio, 3),
           "commit_sha": sha,
           "confidence": "high" if ratio >= 0.9 else "medium" if ratio >= 0.7 else "low"}
    res.update(comment_map(d, sha, path, start, end, lines, comments, tree))
    res["path_at_commit"] = path
    return res


def comment_map(d, sha, path, start, end, lines, comments, tree=None):
    """Map dataset comments onto the file as seen at `sha` (AST-aware)."""
    if tree is None:
        tree = astjava.parse("\n".join(lines).encode())
    method = astjava.locate_method(tree, start)
    hi = max(end, method["end"] if method else 0)
    lo = L.doc_start(lines, start) - 1
    mask = astjava.comment_lines(tree)
    out_comments = []
    used = set()
    for r in comments:
        cl = [norm(x) for x in (r.get("comment") or "").splitlines() if norm(x)]
        lines_abs, kind = L.map_comment(cl, lines, start, end, used=used,
                                        lo=lo, hi=hi, is_comment=mask)
        used.update(lines_abs)
        if not lines_abs:
            out_comments.append({"comment_id": r.get("comment_id"),
                                 "comment": r.get("comment"),
                                 "classification": r.get("classification"),
                                 "lines": []})
            continue
        blames = [blame_at(d, sha, path, ln, ln) for ln in lines_abs[:12]]
        first = next((b for b, ln in zip(blames, lines_abs) if core(lines[ln - 1])),
                     blames[0] if blames else {})
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
    out = {"comments": out_comments, "n_mapped": len(mapped)}
    if mapped and mapped[0].get("commit"):
        out["commit"] = mapped[0]["commit"]
    return out


def collect(d: Path, anchor_group, ref_ne, seen):
    """Commits (and files) where one of the anchor lines was added or removed."""
    cands = []
    for a in anchor_group:
        if not a:
            continue
        # --name-only avoids materialising every diff: on a big history the
        # patch output is what made `git log -S` take tens of minutes.
        p = run(["git", "log", "--all", "--name-only", "--format=C %H",
                 f"-S{a}", "--", "*.java"], d, timeout=1800)
        if p.returncode != 0:
            continue
        sha = None
        for line in p.stdout.splitlines():
            m = HEADER.match(line)
            if m:
                sha = m.group(1)
                continue
            path = line.strip()
            if not sha or not path.endswith(".java"):
                continue
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
    p = run(["git", "-c", "core.quotepath=false", "blame", "--line-porcelain",
             "-L", f"{start},{end}", sha, "--", rel],
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

    if "--remap" in sys.argv:
        # re-map comments of existing ok_history rows against their snapshot;
        # no history search, so this takes seconds instead of minutes
        n = 0
        for i, r in rows.items():
            if r.get("status") != "ok_history":
                continue
            d = repo_dir(r["repo"])
            p = run(["git", "show", f"{r['commit_sha']}:{r['path']}"], d)
            if p.returncode != 0:
                continue
            lines = p.stdout.splitlines()
            tree = astjava.parse("\n".join(lines).encode())
            m = astjava.locate_method(tree, r["start"])
            start = m["start"] if m and m["start"] < r["start"] else r["start"]
            end = max(r.get("end") or start, m["end"] if m else 0)
            r.update({"start": start, "end": end})
            r.update(comment_map(d, r["commit_sha"], r["path"], start, end,
                                 lines, comments.get(i, []), tree))
            n += 1
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(json.dumps(rows[k], ensure_ascii=False) + "\n"
                               for k in sorted(rows)))
        tmp.replace(path)
        print(f"re-mapped {n} ok_history functions")
        return

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
