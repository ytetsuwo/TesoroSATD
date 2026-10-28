"""Audit how the located function compares with the dataset's own declaration.

`14_check_span.py` checks comment placement; this script checks the function
itself, using the Java AST:

  - how far the aligned `start` is from the method declaration (0 = the start is
    the declaration line, >0 = the dataset snippet is a fragment of a longer
    method, <0 = the start is a comment line above it),
  - whether the dataset's method name and the AST's method name agree,
  - whether the file parses without errors.

    python3 scripts/15_ast_analysis.py            # summary + mismatches
    python3 scripts/15_ast_analysis.py --json     # work/ast_analysis.jsonl
"""
from __future__ import annotations

import collections
import json
import re
import sys

import astjava
from common import OUT, WORK, git, load_tesoro, repo_dir

NAME = re.compile(r"([A-Za-z_$][\w$]*)\s*$")


def decl_name(code: str):
    """Method name on the dataset declaration line (same rule as 03_locate)."""
    for line in code.splitlines():
        s = line.strip()
        if not s or s.startswith(("@", "//", "*", "/*", "}")):
            continue
        if "(" not in s or s.endswith(";"):
            continue
        m = NAME.search(s[:s.index("(")])
        if m:
            return m.group(1)
    return None


def source(f):
    d = repo_dir(f["repo"])
    if f.get("at_head", True):
        return (d / f["path"]).read_bytes()
    p = git(d, "show", f"{f.get('commit_sha') or f.get('commit')}:{f['path']}")
    return p.stdout.encode() if p.returncode == 0 else b""


def main() -> int:
    funcs, comments = load_tesoro()
    shifts: collections.Counter = collections.Counter()
    mismatches, unparsed, records = [], [], []
    for line in open(OUT / "functions_located.jsonl"):
        f = json.loads(line)
        if f.get("status") not in ("ok", "ok_history") or not f.get("start"):
            continue
        try:
            src = source(f)
        except OSError:
            continue
        if not src:
            continue
        tree = astjava.parse(src)
        errs = astjava.errors(tree)
        m = astjava.locate_method(tree, f["start"])
        code = next((c.get("code") for c in comments.get(f["id"], [])
                     if c.get("code")), "")
        ds_name = decl_name(code)
        delta = f["start"] - m["start"] if m else None
        if delta is not None:
            shifts[delta] += 1
        ok_name = bool(m and ds_name and m["name"] == ds_name)
        if errs:
            unparsed.append(f["id"])
        if m and ds_name and not ok_name:
            mismatches.append((f["id"], f["repo"], ds_name, m["name"], delta,
                               f["confidence"]))
        records.append({"id": f["id"], "repo": f["repo"], "path": f["path"],
                        "start": f["start"], "delta": delta,
                        "dataset_name": ds_name,
                        "ast_name": m["name"] if m else None,
                        "name_ok": ok_name,
                        "method": m,
                        "parse_errors": len(errs)})

    inside = sum(n for d, n in shifts.items() if d == 0)
    print(f"functions parsed: {len(records)}  (parse errors: {len(unparsed)} files)")
    print(f"start on the declaration: {inside}   shifted: {len(records) - inside}")
    print(f"delta distribution: {dict(sorted(shifts.items()))}")
    print(f"name mismatch: {len(mismatches)}")
    for row in mismatches:
        print(f"  {row[0]:6} {row[1][:38]:38} dataset={row[2]:28} ast={row[3]:28} "
              f"delta={row[4]:3} {row[5]}")

    if "--json" in sys.argv:
        with open(WORK / "ast_analysis.jsonl", "w") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"\nwrote {WORK / 'ast_analysis.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
