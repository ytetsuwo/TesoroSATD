"""Blob-less full-history clones of every Tesoro repository.

Each clone keeps the complete commit/tree history (needed for `git blame`) but
downloads file contents lazily; only `*.java` blobs of HEAD are materialised via
sparse checkout so the SATD comments can be grepped locally.

State is checkpointed in work/clone_state.jsonl, so the script can be re-run.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from common import REPOS, WORK, git_env, head_of, load_tesoro, repo_dir

STATE = WORK / "clone_state.jsonl"
LOCK_FILE = WORK / ".clone_lock"


def load_state():
    state = {}
    if STATE.exists():
        for line in STATE.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                state[r["repo"]] = r
    return state


def save_state(state):
    tmp = WORK / "clone_state.jsonl.tmp"
    tmp.write_text("".join(json.dumps(state[k], ensure_ascii=False) + "\n" for k in sorted(state)))
    tmp.replace(STATE)


def clone(repo: str, meta: dict) -> dict:
    d = repo_dir(repo)
    env = git_env()
    t0 = time.time()
    if not (d / ".git").exists():
        if d.exists():
            shutil.rmtree(d)
        d.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(
            ["git", "clone", "--filter=blob:none", "--no-checkout", "--single-branch",
             "--quiet", f"https://github.com/{repo}.git", str(d)],
            capture_output=True, text=True, timeout=5400, env=env,
        )
        if p.returncode != 0:
            shutil.rmtree(d, ignore_errors=True)
            return {"repo": repo, "status": "clone_failed", "err": p.stderr.strip()[:300],
                    "sec": round(time.time() - t0, 1)}

    # Materialise only Java sources of the default branch.
    subprocess.run(["git", "-C", str(d), "sparse-checkout", "init", "--no-cone"],
                   capture_output=True, text=True, env=env)
    subprocess.run(["git", "-C", str(d), "sparse-checkout", "set", "--no-cone", "*.java"],
                   capture_output=True, text=True, env=env)
    p = subprocess.run(["git", "-C", str(d), "checkout", "--quiet", "HEAD"],
                       capture_output=True, text=True, timeout=3600, env=env)
    if p.returncode != 0:
        # Keep the clone: history is still usable for a later retry.
        return {"repo": repo, "status": "checkout_failed", "err": p.stderr.strip()[:300],
                "sec": round(time.time() - t0, 1)}

    head = head_of(d)
    njava = sum(1 for _ in d.rglob("*.java"))
    return {"repo": repo, "status": "ok", "head": head, "java": njava,
            "sec": round(time.time() - t0, 1)}


def main():
    only = sys.argv[1:] or None
    funcs, _ = load_tesoro()
    meta = json.loads((WORK / "repo_meta.json").read_text())
    repos = sorted({f["repo"] for f in funcs.values()})
    repos = [r for r in repos if not meta.get(r, {}).get("deleted")]
    if only:
        repos = [r for r in repos if r in set(only)]

    state = load_state()
    todo = [r for r in repos if state.get(r, {}).get("status") != "ok"]
    # Big repos first: they dominate the wall clock.
    todo.sort(key=lambda r: -(meta.get(r, {}).get("size") or 0))
    print(f"to clone: {len(todo)} / {len(repos)}", flush=True)

    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(clone, r, meta.get(r, {})): r for r in todo}
        for fut in as_completed(futs):
            repo = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                rec = {"repo": repo, "status": "error", "err": str(e)[:300]}
            state[repo] = rec
            done += 1
            save_state(state)
            if rec["status"] != "ok":
                print(f"[{done}/{len(todo)}] {rec['status']}: {repo} {rec.get('err','')[:120]}", flush=True)
            elif done % 25 == 0:
                el = time.time() - t0
                print(f"[{done}/{len(todo)}] {el/60:.1f} min elapsed, "
                      f"{el/done:.1f}s/repo avg | last: {repo} java={rec.get('java')}", flush=True)

    bad = {k: v for k, v in state.items() if v.get("status") != "ok"}
    print(f"\nfinished in {(time.time()-t0)/60:.1f} min; ok={len(state)-len(bad)} failures={len(bad)}")
    for k, v in list(bad.items())[:20]:
        print(" ", v.get("status"), k, v.get("err", "")[:100])


if __name__ == "__main__":
    main()
