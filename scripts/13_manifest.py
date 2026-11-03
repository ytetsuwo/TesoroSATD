"""Write out/repo_manifest.csv: where every clone in repos/ came from.

One row per directory under repos/ (975 at the time of writing):

  repo        directory name, also the key used in out/*.csv
  kind        github    original repository cloned from GitHub by 02_clone_repos.py
              swh       restored from a Software Heritage vault snapshot by 10_swh_batch.py
              mirror    clone of a different, still-live repository that supplies the code
              symlink   alias of another directory in repos/
  source      for github/mirror the clone URL, for swh "swh vault" (see swhid)
  swhid       Software Heritage snapshot id restored into the directory (kind=swh)
  head        short HEAD commit
  swh_status  last status recorded for this repo in work/swh_state.jsonl
  swh_visit   snapshot date reported by SWH (kind=swh only)

Usage:
    python3 scripts/13_manifest.py                # write out/repo_manifest.csv for every directory
    python3 scripts/13_manifest.py <repo> ...     # print the same rows for a few directories
"""
from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOS = ROOT / "repos"
WORK = ROOT / "work"
OUT = ROOT / "out" / "repo_manifest.csv"


def swh_state() -> dict[str, dict]:
    """Last line per repo wins: work/swh_state.jsonl is append-only."""
    state: dict[str, dict] = {}
    p = WORK / "swh_state.jsonl"
    if not p.exists():
        return state
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("repo"):
            state[r["repo"]] = r
    return state


def git(d: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(d), *args], capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def classify(repo: str, d: Path, remote: str, st: dict) -> tuple[str, str]:
    if d.is_symlink():
        return "symlink", str(Path(d.readlink()))
    if remote.startswith(str(WORK)):
        return "swh", "swh vault"
    # GitHub remote whose owner/name differs from the directory = stand-in for a dead repo.
    if remote.removesuffix(".git").rstrip("/").endswith(repo.replace("__", "/", 1)):
        return "github", remote
    return "mirror", remote


def main(argv: list[str]) -> int:
    state = swh_state()
    names = argv or sorted(p.name for p in REPOS.iterdir())
    rows = []
    for repo in names:
        d = REPOS / repo
        st = state.get(repo.replace("__", "/", 1), {})
        remote = "" if d.is_symlink() else git(d, "config", "--get", "remote.origin.url")
        kind, src = classify(repo, d, remote, st)
        rows.append({
            "repo": repo,
            "kind": kind,
            "source": src,
            "swhid": st.get("swhid", "") if kind == "swh" else "",
            "head": "" if d.is_symlink() else git(d, "rev-parse", "HEAD"),
            "swh_status": st.get("status", ""),
            "swh_visit": st.get("visit", ""),
        })
    if argv:                      # 引数指定時は標準出力だけ（全件の CSV を壊さない）
        w = csv.DictWriter(sys.stdout, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    print(f"wrote {OUT} ({len(rows)} rows)")
    for k, n in sorted(kinds.items()):
        print(f"  {k:8} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
