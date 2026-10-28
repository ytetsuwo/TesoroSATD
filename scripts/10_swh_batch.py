#!/usr/bin/env python3
"""Second attempt for the big repositories: request all cooks at once, then poll.

09_swh.py recovers one repository per worker and blocks on the cook, so a slow
cook starves the queue.  Here every pending repository is submitted in one go,
then polled in rounds; whatever is ready is downloaded and cloned, the rest is
retried next round.

Usage: python3 scripts/10_swh_batch.py [repo ...]      (default: cook_failed)
"""
from __future__ import annotations

import importlib.util
import json
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("swh", HERE / "09_swh.py")
S = importlib.util.module_from_spec(spec)
ARGV = sys.argv[1:]
spec.loader.exec_module(S)

ROUNDS = int(__import__("os").environ.get("ROUNDS", "10"))
GAP = int(__import__("os").environ.get("GAP", "900"))     # seconds between rounds
STATE = S.STATE


def record(rec):
    with open(STATE, "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("  " + rec["status"][:40] + " " + rec["repo"], flush=True)


def load_state():
    st = {}
    if STATE.exists():
        for line in STATE.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                st[r["repo"]] = r
    return st


def fetch(repo: str, swhid: str, date: str):
    url = S.cook(swhid)
    if not url:
        return {"repo": repo, "status": "cook_failed", "swhid": swhid}
    slug = re.sub(r"[^A-Za-z0-9]+", "_", repo)
    tgz = S.TMP / f"{slug}.tar.gz"
    err = S.download(url, tgz)
    if err:
        return {"repo": repo, "status": err, "swhid": swhid}
    out = S.TMP / slug
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    p = S.run(["tar", "xzf", str(tgz), "-C", str(out)], S.TMP, timeout=3600)
    if p.returncode != 0:
        return {"repo": repo, "status": "untar_failed", "swhid": swhid}
    bares = list(out.glob("*.git"))
    if not bares:
        return {"repo": repo, "status": "no_bare_repo", "swhid": swhid}
    res = S.make_clone(repo, bares[0])
    tgz.unlink(missing_ok=True)
    shutil.rmtree(out, ignore_errors=True)
    return {"repo": repo, "status": "ok" if res.startswith("ok:") else res,
            "swhid": swhid, "visit": date,
            "java": res[4:].strip() if res.startswith("ok:") else ""}


def main():
    st = load_state()
    if ARGV:
        repos = ARGV
    else:
        repos = sorted(r for r, v in st.items() if v["status"] != "ok"
                       and v["status"] != "not_archived")
    snaps: dict[str, tuple] = {}
    for repo in repos:
        if st.get(repo, {}).get("status") == "ok":
            continue
        snap = S.snapshot(repo)
        if snap == "error" or not snap:
            print(f"  skip {repo}: {snap}", flush=True)
            continue
        snaps[repo] = snap
    print(f"{len(snaps)} repositories to cook, {ROUNDS} rounds", flush=True)

    # submit everything once; the vault keeps cooking between rounds
    pending = {}
    for repo, (swhid, date) in snaps.items():
        s, t = S.api(f"{S.BASE}/vault/git-bare/{swhid}/", "POST")
        status = (t or {}).get("status", f"http_{s}")
        print(f"  submit {repo}: {status}", flush=True)
        if status in ("done",):
            record(fetch(repo, swhid, date))
        elif status in ("new", "pending"):
            pending[repo] = (swhid, date)
        else:
            record({"repo": repo, "status": "failed", "swhid": swhid})

    for rnd in range(1, ROUNDS + 1):
        if not pending:
            break
        print(f"round {rnd}: {len(pending)} pending, waiting {GAP}s", flush=True)
        time.sleep(GAP)
        for repo, (swhid, date) in list(pending.items()):
            s, t = S.api(f"{S.BASE}/vault/git-bare/{swhid}/")
            status = (t or {}).get("status", f"http_{s}")
            if status == "done":
                print(f"  ready {repo}", flush=True)
                record(fetch(repo, swhid, date))
                pending.pop(repo)
            elif status == "failed":
                print(f"  failed {repo}: {(t or {}).get('error', '')[:80]}", flush=True)
                record({"repo": repo, "status": "failed", "swhid": swhid})
                pending.pop(repo)
            else:
                print(f"  {status} {repo}", flush=True)
    print(f"done, {len(pending)} still cooking", flush=True)


if __name__ == "__main__":
    main()
