#!/usr/bin/env python3
"""Download every blob of every clone, so `git blame` and `git log -S` work offline.

The clones made by 02_clone_repos.py are partial (`--filter=blob:none`): they
have all commits and trees but fetch blobs on demand over HTTP, which makes
blame slow and dependent on the network. This script drops the promisor config
and re-fetches the repository in one pack.

State is kept in work/backfill_all.jsonl, so a re-run skips finished
repositories.

Usage: python3 scripts/11_backfill_all.py [--check] [repo ...]
       --check   only report how many clones are still partial
"""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from common import REPOS, WORK, run

MARK = WORK / "backfill_all.jsonl"


def state() -> dict:
    if not MARK.exists():
        return {}
    out = {}
    for line in MARK.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["repo"]] = r
    return out


def is_partial(d: Path) -> bool:
    p = run(["git", "config", "--get", "remote.origin.partialclonefilter"], d)
    return bool((p.stdout or "").strip())


def size_gb(d: Path) -> float:
    p = run(["du", "-sk", str(d / ".git")], d)
    try:
        return int(p.stdout.split()[0]) / 1024 / 1024
    except (IndexError, ValueError):
        return 0.0


def backfill(d: Path):
    name = d.name
    if not (d / ".git").exists():
        return {"repo": name, "status": "no_clone"}
    if not is_partial(d):
        return {"repo": name, "status": "already", "gb": round(size_gb(d), 2)}
    run(["git", "config", "--unset", "remote.origin.partialclonefilter"], d)
    run(["git", "config", "--unset", "remote.origin.promisor"], d)
    p = run(["git", "fetch", "--refetch", "--quiet", "origin"], d, timeout=10800)
    if p.returncode != 0:
        return {"repo": name, "status": f"fetch_failed: {(p.stderr or '').strip()[:120]}"}
    f = run(["git", "fsck", "--connectivity-only"], d, timeout=7200)
    if f.returncode != 0:
        return {"repo": name, "status": f"fsck: {(f.stderr or '').strip()[:120]}"}
    return {"repo": name, "status": "ok", "gb": round(size_gb(d), 2)}


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--check"]
    check = "--check" in sys.argv

    clones = sorted(p for p in REPOS.iterdir() if (p / ".git").exists())
    if args:
        clones = [p for p in clones if p.name in args or p.name.replace("__", "/") in args]

    if check:
        partial = [p for p in clones if is_partial(p)]
        print(f"{len(clones)} clones, {len(partial)} still partial, "
              f"{len(clones) - len(partial)} complete", flush=True)
        for p in partial[:20]:
            print("  partial", p.name, flush=True)
        return

    st = state()
    todo = [p for p in clones if st.get(p.name, {}).get("status") not in ("ok", "already")]
    print(f"{len(todo)} repositories to backfill "
          f"({len(clones) - len(todo)} already done)", flush=True)

    ok = 0
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(backfill, p): p for p in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                rec = {"repo": futs[fut].name, "status": f"error: {e}"}
            if rec["status"] in ("ok", "already"):
                ok += 1
            print(f"[{i}/{len(todo)}] {rec['status'][:40]:40s} "
                  f"{rec.get('gb', 0):6.1f}GB  {rec['repo']}", flush=True)
            with open(MARK, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"\nbackfilled {ok}/{len(todo)}")


if __name__ == "__main__":
    main()
