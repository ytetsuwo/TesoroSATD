#!/usr/bin/env python3
"""Pin (or check) every clone at the commit recorded in out/repo_manifest.csv.

The locator reads each clone's HEAD, so upstream movement changes the result.
Run this with `--pin` before a reproduction run, or `--check` to see drift.

    python3 scripts/00_pin_repos.py --check
    python3 scripts/00_pin_repos.py --pin
"""
from __future__ import annotations

import csv
import subprocess
import sys

from common import OUT, repo_dir


def head(d) -> str:
    p = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"],
                       capture_output=True, text=True)
    return p.stdout.strip()


def main() -> int:
    pin = "--pin" in sys.argv
    rows = list(csv.DictReader(open(OUT / "repo_manifest.csv")))
    drift = missing = pinned = 0
    for r in rows:
        sha = (r.get("head") or "").strip()
        d = repo_dir(r["repo"].replace("__", "/", 1))
        if not sha or not (d / ".git").exists():
            if sha and not d.exists():
                missing += 1
            continue
        cur = head(d)
        if cur == sha:
            pinned += 1
            continue
        drift += 1
        print(f"{'PIN ' if pin else 'DRIFT'} {r['repo']:34} {cur[:10]} != {sha[:10]}")
        if pin:
            p = subprocess.run(["git", "-C", str(d), "checkout", "--quiet", sha],
                               capture_output=True, text=True)
            if p.returncode != 0:
                print(f"  failed: {p.stderr.strip()[:100]}")
    print(f"\nat target: {pinned}  drifted: {drift}  clone missing: {missing}"
          f"{' (pinned)' if pin else ' (use --pin to fix)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
