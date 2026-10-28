"""Collect existence / size / default branch for every repository in Tesoro.

Writes work/repo_meta.json so later steps know which repos are dead and which
are giants that need special handling.
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import WORK, load_tesoro

TOKEN = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()


def api(repo, tries=3):
    url = f"https://api.github.com/repos/{repo}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "tesoro-provenance",
    })
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                d = json.load(r)
            return {k: d.get(k) for k in
                    ("full_name", "size", "default_branch", "archived", "fork",
                     "language", "pushed_at", "parent")}
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return {"deleted": True}
            if e.code in (403, 429) and i + 1 < tries:
                time.sleep(20 * (i + 1))
                continue
            return {"error": f"HTTP {e.code}"}
        except Exception as e:  # network hiccup
            if i + 1 < tries:
                time.sleep(3)
                continue
            return {"error": str(e)[:200]}
    return {"error": "exhausted"}


def main():
    funcs, _ = load_tesoro()
    repos = sorted({f["repo"] for f in funcs.values()})
    cache_path = WORK / "repo_meta.json"
    cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    cache = {k: v for k, v in cache.items() if not v.get("error") and "deleted" not in v or v.get("deleted")}

    todo = [r for r in repos if r not in cache]
    with ThreadPoolExecutor(max_workers=6) as ex:
        for repo, meta in zip(todo, ex.map(api, todo)):
            cache[repo] = meta
    cache_path.write_text(json.dumps(cache, indent=1))

    dead = [r for r in repos if cache[r].get("deleted")]
    err = [r for r in repos if cache[r].get("error")]
    ok = [r for r in repos if r not in dead and r not in err]
    sizes = sorted(((cache[r].get("size") or 0), r) for r in ok)
    print(f"repos: {len(repos)}  alive: {len(ok)}  deleted: {len(dead)}  errors: {len(err)}")
    print(f"total git size (with blobs): {sum(s for s, _ in sizes)/1024/1024:.1f} GB")
    print("largest:")
    for s, r in sizes[-10:][::-1]:
        print(f"  {s/1024:8.1f} MB  {r}")
    if dead:
        (WORK / "dead_repos.txt").write_text("\n".join(dead) + "\n")
    if err:
        print("errors:", err[:10])


if __name__ == "__main__":
    main()
