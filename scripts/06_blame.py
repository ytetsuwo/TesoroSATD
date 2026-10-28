#!/usr/bin/env python3
"""Look up where a Tesoro comment comes from, and blame it.

  python3 scripts/06_blame.py 8431            # one function / comment id
  python3 scripts/06_blame.py --repo owner/name   # every comment of a project
  python3 scripts/06_blame.py 8431 --raw      # raw git blame output

Prints the repository checkout, the file at that commit, the commit itself and
the `git blame` / `git show` commands you can paste.
"""
import csv
import sys

from common import OUT, git, repo_dir


def load():
    with open(OUT / "tesoro_provenance.csv") as f:
        return list(csv.DictReader(f))


def show(r, raw=False):
    d = repo_dir(r["repo"])
    print(f"comment  : {r['comment_id']} ({r['classification']}) of function {r['function_id']}")
    print(f"project  : {r['repo']}")
    print(f"repo     : {d}")
    print(f"file     : {r['path']}" + (f"  (at commit: {r['path_at_commit']})"
                                      if r["path_at_commit"] and r["path_at_commit"] != r["path"] else ""))
    print(f"lines    : {r['comment_line']}-{int(r['comment_line']) + int(r['n_comment_lines']) - 1}"
          if r["comment_line"] else "lines    : (not mapped)")
    if not r["commit_id"]:
        print("commit   : -")
        return
    print(f"commit   : {r['commit_id']}")
    print(f"author   : {r['commit_author']}  {r['commit_date']}")
    print(f"url      : {r['commit_url']}")
    print(f"blame    : {r['blame_url']}")
    n = int(r["n_comment_lines"])
    ln = int(r["comment_line"])
    print()
    print(f"  git -C {d} blame -L {ln},{ln + n - 1} -- {r['path']}")
    print(f"  git -C {d} show {r['commit_id']} -- {r['path_at_commit'] or r['path']}")
    print("\n--- blame ---")
    p = git(d, "blame", "-L", f"{ln},{ln + n - 1}", "--", r["path"])
    print(p.stdout if raw or p.returncode == 0 else p.stderr)
    if not raw:
        print("--- commit ---")
        print(git(d, "show", "--stat", "--format=%H%n%an <%ae>%n%ad%n%s", "-s",
                  r["commit_id"]).stdout)


def main():
    rows = load()
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    raw = "--raw" in sys.argv
    if not args:
        print(__doc__)
        return
    key = args[0]
    if "--repo" in sys.argv:
        sel = [r for r in rows if r["projectname"] == key and r["commit_id"]]
    else:
        sel = [r for r in rows if key in (r["function_id"], r["comment_id"])]
    if not sel:
        print(f"no row for {key}")
        return
    for r in sel:
        show(r, raw)
        print()


if __name__ == "__main__":
    main()
