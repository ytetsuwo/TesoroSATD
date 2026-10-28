"""Locate every Tesoro function in its repository and blame its SATD comments.

Steps per function (1255 of them):
  1. grep the `*.java` working tree for distinctive lines of the function,
  2. align the function text with the file (difflib) to find its exact position,
  3. map every comment of the function onto file lines and `git blame` them.

Output: out/functions_located.jsonl, one row per function.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path

from common import (OUT, WORK, alnum, core, head_of, load_tesoro, mclass, norm,
                    repo_dir, run, strip_marker)

MIN_ANCHOR_LEN = 25
MAX_FILE_CANDIDATES = 40
SIG = re.compile(r"^[0-9a-f]{40} ")
# Token view: `var` inlining, reordering, added branches change the line ratio a
# lot even when the method is plainly the same one. Tokens survive that.
TOKEN = re.compile(r'"[^"]*"|\'[^\']*\'|[A-Za-z_$][\w$]*|\d+|\S')
MIN_TOKENS = 20
# A comment marker anywhere on the line: trailing comments sit after code.
MARKER_ANY = re.compile(r"//|/\*|\*/|^\s*\*")
WORD = re.compile(r"[a-z0-9]+")
FUZZY_MIN = 0.85


def strip_comments(text: str) -> str:
    """Drop // and /* */ comments, keep string literals intact."""
    out, i, n = [], 0, len(text)
    quote = None
    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            out.append(c)
            if c == "\\" and nxt:
                out.append(nxt)
                i += 2
                continue
            if c == quote:
                quote = None
            i += 1
            continue
        if c == "/" and nxt == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if c == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            end = n if j < 0 else j + 2
            # keep the line count: token_match reports file line numbers
            out.append("\n" * text.count("\n", i, end))
            i = end
            continue
        if c in "\"'":
            quote = c
        out.append(c)
        i += 1
    return "".join(out)


def content(toks) -> list:
    """Identifiers, literals and numbers: punctuation says nothing about identity."""
    return [t for t in toks if t[0].isalnum() or t[0] in "_$\"'"]


def tokens(lines) -> list:
    return content(TOKEN.findall(strip_comments("\n".join(lines))))


NOT_NAMES = {"if", "for", "while", "switch", "catch", "new", "return", "synchronized",
             "assert", "super", "this", "try", "do", "else"}


def decl_name(ref) -> tuple:
    """(method name, its index in ref) from the declaration line, or (None, 0).

    The name is the one thing a refactor never changes, so it is both a cheap
    seed for candidates and a veto on token-only matches (two different methods
    can share a lot of boilerplate, a name they cannot).
    """
    for i, t in enumerate(ref):
        s = t.strip()
        if not s or s.startswith(("@", "//", "*", "/*", "}")):
            continue
        if "(" not in s or s.endswith(";"):
            continue
        head = s[:s.index("(")]
        m = re.search(r"([A-Za-z_$][\w$]*)\s*$", head)
        if m and m.group(1) not in NOT_NAMES:
            return m.group(1), i
    return None, 0


def token_match(ref_tok: list, lines, span_lines: int, name: str | None = None):
    """Best in-order occurrence of the reference tokens inside a short span.

    Returns (coverage, start_line). Unlike a fixed line window this tolerates a
    method that grew or shrank: what matters is that its tokens appear in order
    within `span_lines` lines of each other.
    """
    if len(ref_tok) < MIN_TOKENS or not lines:
        return 0.0, 0
    clean = strip_comments("\n".join(lines)).split("\n")
    win_tok, win_line = [], []
    for ln, text in enumerate(clean, 1):
        for t in content(TOKEN.findall(text)):
            win_tok.append(t)
            win_line.append(ln)
    if not win_tok:
        return 0.0, 0
    decl = set()
    if name:
        rx = re.compile(rf"\b{re.escape(name)}\s*\(")
        decl = {ln for ln, text in enumerate(clean, 1) if rx.search(text)}
    sm = SequenceMatcher(None, ref_tok, win_tok, autojunk=False)
    matched = []                                    # (ref index, file line), ordered
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            matched.append((a + k, win_line[b + k]))
    if not matched:
        return 0.0, 0
    # densest run of matched reference tokens whose file lines stay near each other
    best, lo = 0, 0
    start = matched[0][1]
    for hi, (_, line) in enumerate(matched):
        while line - matched[lo][1] > span_lines:
            lo += 1
        if decl and matched[lo][1] not in decl:
            continue                                    # not this method's declaration
        if hi - lo + 1 > best:
            best, start = hi - lo + 1, matched[lo][1]
    if decl and best:
        start = min(decl, key=lambda d: abs(d - start))
    return best / len(ref_tok), start


# ---------------------------------------------------------------- anchors
def anchors_from(lines, limit=10, min_len=MIN_ANCHOR_LEN):
    """Longest, most distinctive non-empty lines."""
    seen, out = set(), []
    for t in lines:
        if len(t) >= min_len and t not in seen:
            seen.add(t)
            out.append(t)
    out.sort(key=len, reverse=True)
    return out[:limit]


# ---------------------------------------------------------------- git helpers
def blame(d: Path, rel: str, start: int, end: int):
    p = run(["git", "blame", "--line-porcelain", "-L", f"{start},{end}", "--", rel],
            d, timeout=600)
    if p.returncode != 0:
        return {}
    sha = author = summary = fname = None
    when = None
    for line in p.stdout.splitlines():
        if SIG.match(line):
            sha = line.split()[0]
        elif line.startswith("author "):
            author = line[7:]
        elif line.startswith("author-time "):
            when = int(line[12:])
        elif line.startswith("summary "):
            summary = line[8:]
        elif line.startswith("filename "):
            fname = line[9:]
        elif line.startswith("\t"):
            break
    return {"sha": sha, "author": author, "author_time": when, "summary": summary,
            "path": fname}


def path_at_commit(d: Path, rel: str, sha: str) -> str:
    """Follow renames backwards to the file name that `sha` saw."""
    p = run(["git", "log", "--follow", "--name-status", "--format=C %H", "--", rel],
            d, timeout=600)
    cur = rel
    for line in p.stdout.splitlines():
        if line.startswith("C "):
            if line[2:] == sha:
                return cur
        elif line[:1] in ("R", "C") and "\t" in line:
            parts = line.split("\t")
            if len(parts) == 3 and parts[2] == cur:
                cur = parts[1]
    return rel


def read_lines(path: Path):
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def grep(d: Path, patterns: list):
    """Search the java working tree for the given lines (fixed strings)."""
    if not patterns:
        return []
    p = subprocess.run(["rg", "--no-ignore-vcs", "--no-heading", "-n", "-F", "-f", "-",
                        "-g", "*.java", "."], cwd=str(d), input="\n".join(patterns) + "\n",
                       capture_output=True, text=True, errors="replace", timeout=300)
    hits = []
    for line in p.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            hits.append((parts[0].lstrip("./"), int(parts[1]), norm(parts[2])))
    return hits


# ---------------------------------------------------------------- locating
REJECT = WORK / "manual_reject.jsonl"


def rejects() -> dict:
    """{id: reason} for matches a human ruled out (duplicated generated tests)."""
    if not REJECT.exists():
        return {}
    out = {}
    for line in REJECT.read_text().splitlines():
        if line.strip() and not line.startswith("//"):
            r = json.loads(line)
            out[r["id"]] = r.get("reason", "rejected")
    return out


def locate(repo: str, func: dict, comments: list) -> dict:
    d = repo_dir(repo)
    base = {"id": func["id"], "repo": repo, "label": func.get("label"),
            "n_comments": len(comments)}
    if not (d / ".git").exists():
        return {**base, "status": "repo_missing"}

    # Representative text: the record whose code is the most complete.
    rep = max(comments, key=lambda r: len(r.get("code") or "")) if comments else None
    code = (rep or {}).get("code") or func.get("original_code") or ""
    ref = [norm(x) for x in code.splitlines()]
    ref_ne = [x for x in ref if x]
    if not ref_ne:
        return {**base, "status": "no_anchor"}
    ref_tok = tokens(ref)
    span = max(40, 2 * len(ref))
    name, decl_idx = decl_name(ref)

    comment_lines_all = []
    for r in comments:
        comment_lines_all += [norm(x) for x in (r.get("comment") or "").splitlines()]
    anchors = anchors_from(ref_ne) or sorted(ref_ne, key=len, reverse=True)[:3]
    anchors += [a for a in anchors_from([x for x in comment_lines_all if x], 6) if a not in anchors]
    hits = grep(d, anchors)
    if not hits:
        # the dataset text may be an older shape of the function: relax the
        # anchors (shorter lines, then everything) before giving up
        for relaxed in (ref_ne[:3] + anchors_from(ref_ne, min_len=12),
                        ref_ne[:5] + sorted(ref_ne, key=len, reverse=True)[:4],
                        ref_ne[:40]):
            hits = grep(d, relaxed)
            if hits:
                anchors = relaxed
                break
    if not hits:
        return {**base, "status": "not_at_head", "head": head_of(d)}

    # --- candidate alignments (file, start), seeded by the longest anchor line
    index_of = {}
    for i, t in enumerate(ref):
        index_of.setdefault(t, []).append(i)
    seed = anchors[0]
    cands = {(path, ln - idx) for path, ln, text in hits if text == seed
             for idx in index_of.get(seed, [])[:3] if ln - idx >= 1}
    if not cands:
        cands = {(path, ln - idx) for path, ln, text in hits
                 for idx in index_of.get(text, [])[:1] if ln - idx >= 1}
    if not cands:
        # No reference line survived into any hit: fall back to the raw hit
        # positions (comment-only evidence). Aligned windows still get scored.
        cands = {(path, ln) for path, ln, _ in hits}
    if not cands:
        return {**base, "status": "not_at_head", "head": head_of(d)}
    # The declaration name seeds candidates even when every anchor line was
    # rewritten: `name(` is short, but it pins the window to a real method.
    if name:
        for path, ln, text in grep(d, [name + "("]):
            if name + "(" in text:
                cands.add((path, max(1, ln - decl_idx)))
    # scan the files with the most anchor hits first: token scoring is per file
    per_path = Counter(path for path, _, _ in hits)
    order = {p: i for i, (p, _) in enumerate(per_path.most_common())}
    cands = sorted(cands, key=lambda c: (order.get(c[0], 999), c))[:MAX_FILE_CANDIDATES * 10]

    best = None
    cache, tok_cache = {}, {}
    for path, start in cands:
        lines = cache.get(path)
        if lines is None:
            lines = cache[path] = read_lines(d / path)
        if not lines:
            continue
        win = [norm(x) for x in lines[start - 1:start - 1 + len(ref)]]
        win_ne = [x for x in win if x]
        line_ratio = (SequenceMatcher(None, ref_ne, win_ne, autojunk=False).ratio()
                      if win_ne else 0.0)
        # Token coverage survives refactoring that changes the line count
        # (`var`, extra branches, reordering); only the top files are worth it.
        if path not in tok_cache:
            tok_cache[path] = (token_match(ref_tok, lines, span, name)
                               if len(tok_cache) < MAX_FILE_CANDIDATES else (0.0, start))
        cov, tok_start = tok_cache[path]
        # A match must start at the declaration of this method (a few lines of
        # annotations are fine); otherwise it is shared boilerplate — generated
        # tests and framework stubs look alike without sharing a name.
        # The name guards token matches only: shared boilerplate (generated
        # tests, framework stubs) matches many files, while a line-for-line
        # copy is evidence enough on its own.
        if name and cov > line_ratio and cov < 0.9:
            if not any(re.search(rf"\b{re.escape(name)}\s*\(", x)
                       for x in lines[max(0, tok_start - 2):tok_start + 3]):
                cov = 0.0
        score = max(line_ratio, cov)
        if best is None or score > best[0]:
            best = (score, line_ratio, cov, path, start if line_ratio >= cov else tok_start,
                    lines, win)
    if best is None:
        return {**base, "status": "unreadable", "head": head_of(d)}

    ratio, line_ratio, cov, path, start, lines, win = best
    # Same method text in several files (duplicated generated tests) has no
    # single provenance: an almost-tied rival means the match is ambiguous.
    rival = ""
    if cov and cov > line_ratio and cov < 0.9:
        for p2, (c2, _) in tok_cache.items():
            if p2 != path and c2 >= cov:
                rival = p2
                break
    if ratio < 0.45 or rival:
        return {**base, "status": "weak_match", "path": path, "ratio": round(ratio, 3),
                **({"rival": rival} if rival else {}),
                "line_ratio": round(line_ratio, 3), "tok_ratio": round(cov, 3),
                "head": head_of(d)}

    res = {**base, "status": "ok", "path": path, "start": start,
           "end": min(len(lines), start + len(ref) - 1 + (20 if cov > line_ratio else 0)),
           "ratio": round(ratio, 3),
           "line_ratio": round(line_ratio, 3), "tok_ratio": round(cov, 3),
           "match": "line" if line_ratio >= cov else "token",
           "head": head_of(d),
           "confidence": "high" if ratio >= 0.9 else "medium" if ratio >= 0.7 else "low"}

    # --- map each comment onto file lines
    out_comments, used = [], set()
    for r in comments:
        cl = [norm(x) for x in (r.get("comment") or "").splitlines() if norm(x)]
        lines_abs, kind = map_comment(cl, lines, start, start + len(ref) - 1, used=used)
        used.update(lines_abs)
        if not lines_abs:
            out_comments.append({"comment_id": r.get("comment_id"),
                                 "comment": r.get("comment"),
                                 "classification": r.get("classification"),
                                 "lines": []})
            continue
        blames = [blame(d, path, ln, ln) for ln in lines_abs[:12]]
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
        res["path_at_commit"] = path_at_commit(d, path, mapped[0]["commit"])
    return res


def cbody(line: str) -> str:
    """Comment text on a line (markers stripped, code before it dropped)."""
    m = MARKER_ANY.search(line)
    return MARKER_ANY.sub(" ", line[m.start():]) if m else ""


def words(text: str) -> list[str]:
    return WORD.findall(text.lower())


def map_comment(cl, lines, start, end, span=150, used=None):
    """Map a dataset comment onto file lines.

    Returns (lines_abs, kind) where kind is "exact", "structural", "fuzzy" or None.
    Comments above the function are often outside the aligned code window and
    may have been edited since the crawl, so a same-shape block right above the
    function is accepted as a structural match. When the wording itself changed,
    the closest comment run in the window is accepted as "fuzzy" (token overlap
    >= FUZZY_MIN), so the row records the commit that wrote the current wording.
    """
    if not cl:
        return [], None
    lo, hi = max(0, start - 1 - span), min(len(lines), end + span)
    n = len(cl)
    exact, struct = [], []
    for p in range(lo, hi - n + 1):
        block = lines[p:p + n]
        cores_eq = sum(1 for a, b in zip(cl, block) if core(a) and core(a) == core(b))
        if cores_eq == n and all(core(a) for a in cl):
            exact.append(p)
        elif n <= 1 or (mclass(block[0]) and mclass(block[0]) == mclass(cl[0])
                        and mclass(block[-1]) == mclass(cl[-1])):
            if cores_eq >= 1 or (n == 1 and core(cl[0]) and core(cl[0]) in core(block[0])):
                struct.append(p)
    if exact:
        p = min(exact, key=lambda q: abs((q + n) - start))
        return list(range(p + 1, p + n + 1)), "exact"
    if struct:
        p = min(struct, key=lambda q: abs((q + n) - start))
        return list(range(p + 1, p + n + 1)), "structural"

    # --- fuzzy: same comment, reworded since the crawl
    want = words(" ".join(cl))
    if len(want) < 8:
        return [], None
    used = used or set()
    best = None
    p = lo
    while p < hi:
        if p in used or not cbody(lines[p]):
            p += 1
            continue
        q, parts, first, last = p, [], None, None
        while q < hi and len(parts) <= n + 4:
            body = cbody(lines[q])
            if not body:
                if lines[q].strip():      # real code: the comment run ends
                    break
                q += 1                    # blank line: the block continues
                continue
            if first is None:
                first = q
            last = q
            parts.append(body)
            q += 1
        got = words(" ".join(parts))
        if got:
            r = SequenceMatcher(None, want, got, autojunk=False).ratio()
            if r >= FUZZY_MIN and (best is None or r > best[0]):
                best = (r, first, last)
        p = q
    if best:
        return list(range(best[1] + 1, best[2] + 2)), "fuzzy"
    return [], None


def main():
    funcs, comments = load_tesoro()
    drop = rejects()
    out_path = OUT / "functions_located.jsonl"
    done = {}
    if out_path.exists():
        for line in out_path.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done[r["id"]] = r

    # statuses worth retrying automatically (kept out of `done` results otherwise)
    RETRY = {"error", "unreadable"}
    todo = [i for i in funcs if i not in done or done[i].get("status") in RETRY]
    all_ = "--all" in sys.argv
    only = {a for a in sys.argv[1:] if not a.startswith("--")}
    if all_:
        # re-score every HEAD match; ok_history rows are 08_history's business
        todo = [i for i in funcs if done.get(i, {}).get("status") != "ok_history"]
    if only:
        # explicit ids / repos force a re-run regardless of previous status
        todo = [i for i in funcs if funcs[i]["repo"] in only or str(i) in only]
    print(f"to locate: {len(todo)} / {len(funcs)}", flush=True)

    t0 = time.time()
    n = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(locate, funcs[i]["repo"], funcs[i], comments.get(i, [])): i
                for i in todo}
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                rec = {"id": i, "repo": funcs[i]["repo"], "status": "error",
                       "err": str(e)[:300]}
            if i in drop:
                rec = {"id": i, "repo": funcs[i]["repo"], "status": "weak_match",
                       "reason": drop[i], "ratio": 0.0, "head": rec.get("head")}
            done[i] = rec
            n += 1
            if rec["status"] != "ok" or n % 50 == 0:
                print(f"[{n}/{len(todo)}] {rec['status']:14s} {rec.get('ratio','')} "
                      f"{rec.get('repo')} {rec.get('path','')}", flush=True)
            if n % 200 == 0:
                save(out_path, done)

    save(out_path, done)
    print("\nstatus:", Counter(r["status"] for r in done.values()))
    print("confidence:", Counter(r.get("confidence") for r in done.values() if r["status"] == "ok"))
    print(f"elapsed {(time.time()-t0)/60:.1f} min")


def save(path, done):
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(done[k], ensure_ascii=False) + "\n" for k in sorted(done)))
    tmp.replace(path)


if __name__ == "__main__":
    main()
