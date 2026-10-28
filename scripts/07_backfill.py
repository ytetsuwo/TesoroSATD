#!/usr/bin/env python3
"""Backfill blobs into the partial clones so history can be searched offline.

The clones made by 02_clone_repos.py have all commits and trees but no blobs
(`--filter=blob:none`); every `git log -S` would fetch blobs one by one over
HTTP. Dropping the promisor config and re-fetching downloads them in one pack,
after which `git log -S` / `git grep <sha>` are local and fast.

Usage: python3 scripts/07_backfill.py [repo ...]
       (default: repositories of functions not found at HEAD)
"""
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from common import OUT, REPOS, WORK, git, repo_dir, run

MARK = WORK / "backfill_done.txt"


def targets():
    rows = [json.loads(l) for l in (OUT / "functions_located.jsonl").read_text().splitlines() if l.strip()]
    seen = []
    for r in rows:
        if r["status"] in ("not_at_head", "weak_match") and r["repo"] not in seen:
            seen.append(r["repo"])
    return seen


def backfill(repo):
    d = repo_dir(repo)
    if not (d / ".git").exists():
        return repo, "no_clone", 0
    if str(d) in done():
        return repo, "cached", 0
    run(["git", "config", "--unset", "remote.origin.partialclonefilter"], d)
    run(["git", "config", "--unset", "remote.origin.promisor"], d)
    p = run(["git", "fetch", "--refetch", "--quiet", "origin"], d, timeout=7200)
    if p.returncode != 0:
        return repo, f"fetch_failed: {(p.stderr or '').strip()[:120]}", 0
    f = run(["git", "fsck", "--connectivity-only"], d, timeout=3600)
    if f.returncode != 0:
        return repo, f"fsck: {(f.stderr or '').strip()[:120]}", 0
    size = int(run(["du", "-sk", str(d / ".git")], d).stdout.split()[0])
    return repo, "ok", size


def done():
    if not MARK.exists():
        return set()
    return set(MARK.read_text().split())


def main():
    repos = sys.argv[1:] or targets()
    print(f"{len(repos)} repositories to backfill", flush=True)
    ok = 0
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(backfill, r): r for r in repos}
        for i, fut in enumerate(as_completed(futs), 1):
            repo, status, size = fut.result()
            print(f"[{i}/{len(repos)}] {status:16s} {size/1024:8.0f}MB  {repo}", flush=True)
            if status in ("ok", "cached"):
                ok += 1
                with open(MARK, "a") as f:
                    f.write(str(repo_dir(repo)) + "\n")
    print(f"\nbackfilled {ok}/{len(repos)}")


if __name__ == "__main__":
    main()
