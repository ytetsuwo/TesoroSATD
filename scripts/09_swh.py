#!/usr/bin/env python3
"""Recover repositories deleted from GitHub through Software Heritage.

For every repository whose functions are `repo_missing`:

  1. ask SWH for its latest full `git` visit,
  2. cook a `git-bare` archive of that snapshot (asynchronous binding),
  3. download and extract the bare repository,
  4. clone it into repos/<owner__name> with `*.java` checked out, exactly like
     scripts/02_clone_repos.py, so 03_locate.py can run unchanged.

State is kept in work/swh_state.jsonl, so a re-run skips finished repositories.
Usage: python3 scripts/09_swh.py [repo ...]
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from common import OUT, REPOS, WORK, git_env, repo_dir, run

BASE = "https://archive.softwareheritage.org/api/1"
UA = {"User-Agent": "tesoro-provenance/1.0 (https://github.com/NamCyan/tesoro)"}
STATE = WORK / "swh_state.jsonl"
TMP = WORK / "swh"
MAX_GB = 4.0           # skip archives bigger than this
POLL_MIN = 180          # give up cooking after this many minutes
LOCK = threading.Lock()          # the API allows 120 requests/hour, so serialise
QUOTA = {"remaining": None, "reset": 0.0}


def track(headers):
    """Remember the anonymous rate-limit budget SWH reports on every reply."""
    try:
        rem = headers.get("X-Ratelimit-Remaining")
        reset = headers.get("X-Ratelimit-Reset")
        if rem is not None:
            QUOTA["remaining"] = int(rem)
        if reset is not None:
            QUOTA["reset"] = float(reset)
    except (TypeError, ValueError):
        pass


def api(url, method="GET", timeout=120, tries=8):
    """One request, waiting politely whenever the hourly quota is exhausted."""
    for attempt in range(tries):
        with LOCK:
            now = time.time()
            if QUOTA["remaining"] is not None and QUOTA["remaining"] <= 1 \
                    and QUOTA["reset"] > now:
                wait = min(QUOTA["reset"] - now + 5, 3600)
                print(f"  quota spent, sleeping {wait / 60:.0f} min", flush=True)
                time.sleep(wait)
            req = urllib.request.Request(url, method=method, headers=UA)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    track(r.headers)
                    return r.status, json.loads(r.read() or b"{}")
            except urllib.error.HTTPError as e:
                track(e.headers)
                if e.code == 429:
                    body = e.read().decode("utf-8", "replace")
                    m = re.search(r"available in (\d+) seconds", body)
                    wait = int(m.group(1)) + 5 if m else 600
                    print(f"  429, sleeping {wait / 60:.0f} min", flush=True)
                    time.sleep(min(wait, 3600))
                    continue
                if e.code in (400, 404):
                    return e.code, None
                time.sleep(15 * (attempt + 1))
            except Exception:  # noqa: BLE001
                time.sleep(15 * (attempt + 1))
    return 0, None


def snapshot(repo: str):
    q = urllib.parse.quote(f"https://github.com/{repo}", safe="")
    s, visits = api(f"{BASE}/origin/{q}/visits/")
    if s == 404:
        return None
    if s != 200 or not isinstance(visits, list):
        return "error"
    cand = [v for v in visits if v.get("type") == "git" and v.get("snapshot")]
    if not cand:
        return None
    full = [v for v in cand if v.get("status") == "full"] or cand
    best = max(full, key=lambda v: v.get("date") or "")
    return "swh:1:snp:" + best["snapshot"], best.get("date")


def cook(swhid: str):
    s, t = api(f"{BASE}/vault/git-bare/{swhid}/", "POST")
    if s != 200 or not t:
        return None
    t0 = time.time()
    while t.get("status") != "done":
        if t.get("status") == "failed" or (time.time() - t0) / 60 > POLL_MIN:
            return None
        time.sleep(10)
        s, t = api(f"{BASE}/vault/git-bare/{swhid}/")
        if s != 200 or not t:
            return None
    return t.get("fetch_url")


def download(fetch_url: str, dest: Path):
    req = urllib.request.Request(fetch_url, headers=UA)
    with urllib.request.urlopen(req, timeout=600) as r:
        total = int(r.headers.get("Content-Length") or 0)
        if total > MAX_GB * 1e9:
            return f"too_big:{total / 1e9:.1f}GB"
        dest.parent.mkdir(parents=True, exist_ok=True)
        got = 0
        with open(dest, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                got += len(chunk)
                if got > MAX_GB * 1e9:
                    f.close()
                    dest.unlink(missing_ok=True)
                    return f"too_big:{got / 1e9:.1f}GB"
                f.write(chunk)
    return None


def make_clone(repo: str, bare: Path):
    dest = repo_dir(repo)
    if dest.exists():
        shutil.rmtree(dest)
    p = run(["git", "clone", "--quiet", "--no-checkout", str(bare), str(dest)],
            WORK, timeout=1800)
    if p.returncode != 0:
        return f"clone_failed: {p.stderr.strip()[:120]}"
    for cmd in (["git", "sparse-checkout", "init", "--no-cone"],
                ["git", "sparse-checkout", "set", "--no-cone", "*.java"],
                ["git", "checkout", "--quiet", "HEAD"]):
        p = run(cmd, dest, timeout=1800)
        if p.returncode != 0:
            return f"checkout_failed: {p.stderr.strip()[:120]}"
    n = len(list(dest.rglob("*.java")))
    return f"ok: {n} java files"


def recover(repo: str):
    if not repo or repo.endswith("/"):
        return {"repo": repo, "status": "bad_name"}
    snap = snapshot(repo)
    if snap == "error":
        return {"repo": repo, "status": "api_error"}
    if not snap:
        return {"repo": repo, "status": "not_archived"}
    swhid, date = snap
    url = cook(swhid)
    if not url:
        return {"repo": repo, "status": "cook_failed", "swhid": swhid}
    slug = re.sub(r"[^A-Za-z0-9]+", "_", repo)
    tgz = TMP / f"{slug}.tar.gz"
    err = download(url, tgz)
    if err:
        return {"repo": repo, "status": err, "swhid": swhid}
    out = TMP / slug
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    p = run(["tar", "xzf", str(tgz), "-C", str(out)], TMP, timeout=3600)
    if p.returncode != 0:
        return {"repo": repo, "status": "untar_failed", "swhid": swhid}
    bares = list(out.glob("*.git"))
    if not bares:
        return {"repo": repo, "status": "no_bare_repo", "swhid": swhid}
    res = make_clone(repo, bares[0])
    tgz.unlink(missing_ok=True)
    shutil.rmtree(out, ignore_errors=True)
    status = "ok" if res.startswith("ok:") else res
    return {"repo": repo, "status": status, "swhid": swhid, "visit": date,
            "java": res[4:].strip() if res.startswith("ok:") else ""}


def load_state():
    if not STATE.exists():
        return {}
    st = {}
    for line in STATE.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            st[r["repo"]] = r
    return st


def main():
    rows = [json.loads(l) for l in (OUT / "functions_located.jsonl").read_text().splitlines()
            if l.strip()]
    repos = sorted({r["repo"] for r in rows if r["status"] == "repo_missing"})
    if len(sys.argv) > 1:
        repos = sys.argv[1:]
    st = load_state()
    todo = [r for r in repos if st.get(r, {}).get("status") != "ok"]
    # archives of very large upstreams cook slowly; recover the many small ones first
    giant = ("chromium", "android_frameworks_base", "openoffice", "netbeans",
             "ghidra", "openjdk", "hadoop", "spark", "flink", "tensorflow", "nifi",
             "cassandra", "lucene", "kafka", "jetty", "groovy", "gradle")
    todo.sort(key=lambda r: (any(g in r.lower() for g in giant), r))
    print(f"{len(todo)} repositories to recover ({len(repos) - len(todo)} done)", flush=True)

    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {ex.submit(recover, r): r for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001
                rec = {"repo": futs[fut], "status": f"error: {e}"}
            print(f"[{i}/{len(todo)}] {rec['status']:20s} {rec['repo']}", flush=True)
            with open(STATE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("done")


if __name__ == "__main__":
    main()
