"""Check that every mapped comment really sits inside the located function.

The function is taken from the Java AST (tree-sitter), not from counting braces:
the located `start` may land inside a method body, and a brace count would then
end at the wrong `}`. A comment is OK when it falls in

  body   the method body (`{` ... `}`)
  decl   the declaration itself (annotations, `throws` clause, signature)
  above  the comment block directly above the declaration (javadoc)

`outside` is anything else: another method, or text that only looks like the
dataset comment. `on_comment` additionally reports whether the mapped lines are
really comment nodes, which catches text that matches a code line.

    python3 scripts/14_check_span.py            # summary + worst offenders
    python3 scripts/14_check_span.py --json     # one record per function to work/span_check.jsonl
"""
from __future__ import annotations

import collections
import re
import json
import sys

import astjava
from common import OUT, WORK, git, repo_dir


def source(repo: str, path: str, at_head: bool, commit: str) -> bytes:
    d = repo_dir(repo)
    if at_head:
        return (d / path).read_bytes()
    p = git(d, "show", f"{commit}:{path}")
    return p.stdout.encode() if p.returncode == 0 else b""


def main() -> int:
    stats: dict[str, collections.Counter] = {}
    shifts: collections.Counter = collections.Counter()
    records = []
    for line in open(OUT / "functions_located.jsonl"):
        f = json.loads(line)
        if f.get("status") not in ("ok", "ok_history") or not f.get("start"):
            continue
        try:
            src = source(f["repo"], f["path"], f.get("at_head", True),
                         f.get("commit_sha") or f.get("commit") or "")
        except OSError:
            continue
        if not src:
            continue
        tree = astjava.parse(src)
        lines = src.splitlines()
        ms = astjava.methods(tree)
        start = f["start"]
        m = astjava.enclosing(tree, start, ms)
        if m is None:
            near = [x for x in ms if abs(x["start"] - start) <= 8]
            m = min(near, key=lambda x: abs(x["start"] - start)) if near else None
        if m is None:
            records.append({"id": f["id"], "repo": f["repo"], "start": start,
                            "method": None, "comments": []})
            continue
        shifts[start - m["start"]] += 1
        doc = astjava.doc_block(tree, m)
        doc_lines = set(range(doc[0]["start"], doc[-1]["end"] + 1)) if doc else set()
        mask = astjava.comment_lines(tree)
        rec = {"id": f["id"], "repo": f["repo"], "start": start,
               "method": m, "shift": start - m["start"], "comments": []}
        # abstract/interface methods have no body: the declaration is the span
        b_start = m["body_start"] or m["start"]
        b_end = m["body_end"] or m["end"]
        for c in f["comments"]:
            if not c.get("lines"):
                continue
            ln, last = c["lines"][0], c["lines"][-1]
            if b_start <= ln and last <= b_end:
                where = "body"
            elif m["start"] <= ln and last < b_start:
                where = "decl"
            elif last < m["start"] and all(x in doc_lines for x in range(ln, last + 1)):
                where = "above"
            else:
                where = "outside"
            # A multi-line `//` block is one node per line, so the test is per line.
            on_comment = all(mask[x] for x in range(ln, last + 1)
                             if x < len(mask) and lines[x - 1].strip())
            kind = c.get("match") or "?"
            stats.setdefault(kind, collections.Counter())[where] += 1
            stats[kind]["not_comment"] += 0 if on_comment else 1
            rec["comments"].append({"comment_id": c["comment_id"], "line": ln,
                                    "kind": kind, "where": where,
                                    "on_comment": on_comment})
        records.append(rec)

    for kind, v in sorted(stats.items()):
        print(f"{kind:10} body {v['body']:5}  decl {v['decl']:3}  above {v['above']:4}  "
              f"outside {v['outside']:3}  not-a-comment {v['not_comment']:3}")

    bad = [(r, c) for r in records for c in r["comments"] if c["where"] == "outside"]
    print(f"\noutside the function: {len(bad)} comment(s) in "
          f"{len({r['id'] for r, _ in bad})} function(s)")
    for r, c in bad[:20]:
        print(f"  {r['id']:6} {c['kind']:10} comment {c['comment_id']:3} line {c['line']:6} "
              f"span {r['method']['start']}-{r['method']['end']}  {r['repo']}")
    notc = [(r, c) for r in records for c in r["comments"] if not c["on_comment"]]
    print(f"\nnot on a comment node: {len(notc)} comment(s) in "
          f"{len({r['id'] for r, _ in notc})} function(s)")
    for r, c in notc[:20]:
        print(f"  {r['id']:6} {c['kind']:10} comment {c['comment_id']:3} line {c['line']:6} "
              f"span {r['method']['start']}-{r['method']['end']}  {r['repo']}")

    moved = sum(n for d, n in shifts.items() if d)
    print(f"\nstart shifted onto a declaration: {moved} function(s) "
          f"(delta distribution {dict(sorted(shifts.items()))})")

    if "--json" in sys.argv:
        with open(WORK / "span_check.jsonl", "w") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nwrote {WORK / 'span_check.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
