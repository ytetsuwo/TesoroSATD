#!/usr/bin/env python3
"""Verify provenance rows: does the claimed comment really sit at the claimed
line, and does the claimed commit really contain it?

  python3 scripts/05_verify.py [N]     random sample (default 12), 3 checks
  python3 scripts/05_verify.py --full  every ok row, text check only
"""
import csv
import random
import sys

from common import OUT, core, git, norm, repo_dir, strip_marker


def rows():
    with open(OUT / "tesoro_provenance.csv") as f:
        return [r for r in csv.DictReader(f)
                if r["status"] in ("ok", "ok_history") and r["comment_line"]]


def content(r):
    """File lines as seen at the commit the row refers to (HEAD or a snapshot)."""
    d = repo_dir(r["repo"])
    if r.get("at_head") == "True":
        return (d / r["path"]).read_text(errors="replace").splitlines()
    ref = r["snapshot_commit"]
    p = git(d, "show", f"{ref}:{r['path']}")
    if p.returncode != 0:
        raise OSError(p.stderr.strip()[:120])
    return p.stdout.splitlines()


def check_text(r):
    """The comment block at comment_line must still look like the dataset text."""
    try:
        lines = content(r)
    except OSError as e:
        return f"read: {e}"
    n = int(r["n_comment_lines"])
    ln = int(r["comment_line"])
    got = lines[ln - 1:ln - 1 + n]
    want = [x for x in r["commenttext"].splitlines() if norm(x)]
    if not got:
        return "line out of range"
    gc = [core(x) for x in got if core(x)]
    wc = [core(x) for x in want if core(x)]
    if r["match"] == "exact":
        return None if [core(x) for x in got] == [core(x) for x in want] else "exact but differs"
    # structural: the block was edited, or the comment is a trailing one on a
    # code line, so one surviving line (as a substring) is expected
    g = " ".join(gc)
    if any(w and w in g for w in wc):
        return None
    return "structural, no shared line"


def check_blame(r):
    """Blame at HEAD must attribute the line to the recorded commit."""
    d = repo_dir(r["repo"])
    n = int(r["n_comment_lines"])
    ln = int(r["comment_line"])
    rev = [] if r.get("at_head") == "True" else [r["snapshot_commit"]]
    p = git(d, "blame", "--line-porcelain", "-L", f"{ln},{ln + n - 1}", *rev, "--", r["path"])
    if p.returncode != 0:
        return f"blame failed: {p.stderr.strip()[:120]}"
    shas = {l.split()[0] for l in p.stdout.splitlines()
            if len(l.split()) and len(l.split()[0]) == 40}
    return None if r["commit_id"] in shas else f"blame {[s[:8] for s in shas]}"


def check_commit(r):
    """The recorded commit must still contain the comment at path_at_commit."""
    d = repo_dir(r["repo"])
    sha, path = r["commit_id"], r["path_at_commit"] or r["path"]
    p = git(d, "show", f"{sha}:{path}")
    if p.returncode != 0:
        return f"show failed: {p.stderr.strip()[:120]}"
    want = [core(x) for x in r["commenttext"].splitlines() if core(x)]
    have = " ".join(core(x) for x in p.stdout.splitlines() if core(x))
    return None if any(w and w in have for w in want) else "comment absent in that commit"


CHECKS = (("text", check_text), ("blame", check_blame), ("show", check_commit))


def main():
    rs = rows()
    if "--full" in sys.argv:
        bad = []
        for r in rs:
            e = check_text(r)
            if e:
                bad.append((r, e))
        print(f"text: {len(rs) - len(bad)} ok / {len(bad)} bad of {len(rs)}")
        for r, e in bad[:30]:
            print(f"  {r['function_id']:>6} {r['repo']} {r['path']}:{r['comment_line']} "
                  f"[{r['match']}] {e}")
        return

    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12
    sample = random.Random(7).sample(rs, n)
    print(f"{'id':>6}  text  blame  show   repo path:line")
    tally = {k: 0 for k, _ in CHECKS}
    for r in sample:
        res = {k: fn(r) for k, fn in CHECKS}
        for k, v in res.items():
            if v is None:
                tally[k] += 1
        flags = "".join("  ok  " if res[k] is None else " BAD  " for k, _ in CHECKS)
        print(f"{r['function_id']:>6} {flags} {r['repo']} {r['path']}:{r['comment_line']} "
              f"[{r['match']} {r['function_ratio']}] {r['commit_short']} {r['commit_date'][:10]}")
        for k, v in res.items():
            if v:
                print(f"       {k}: {v}")
    print("\n" + ", ".join(f"{k} {v}/{len(sample)}" for k, v in tally.items()))


if __name__ == "__main__":
    main()
