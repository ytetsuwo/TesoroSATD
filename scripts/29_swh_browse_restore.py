#!/usr/bin/env python3
"""Rebuild a deleted repository from Software Heritage browse pages.

The SWH Vault can stay in `new` for a snapshot even when its revision and
directory objects are readable.  The browse pages and their raw content
endpoint are not subject to the API rate limit, so this script walks the
directory pages, writes blobs/trees/commits as ordinary git objects, and
verifies every object id against the SWH id.

The history is cut at the oldest reconstructed revision with `.git/shallow`.
This is enough for `git blame`/`git show` on the files present in that range,
without downloading the full (possibly huge) repository.

Example (javacreed/super-simple-stocks):

    python3 scripts/29_swh_browse_restore.py \
        repos/javacreed__super-simple-stocks \
        --head 5cda4140df3b6fa688c2891606fe5c88de4d5afa \
        --keep 7
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = "https://archive.softwareheritage.org"
ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "work" / "tmp" / "swh-browse-cache"
REPO: Path
DIR_CACHE: dict[tuple[str, str], str] = {}


def fetch(url: str) -> bytes:
    CACHE.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(url.encode()).hexdigest()
    path = CACHE / key
    if path.exists():
        return path.read_bytes()
    req = urllib.request.Request(url, headers={"User-Agent": "tesoro-provenance"})
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            data = response.read()
    except urllib.error.HTTPError as error:
        if error.code == 429:
            reset = error.headers.get("X-RateLimit-Reset")
            wait = max(0, int(reset) - int(datetime.now().timestamp())) if reset else 3600
            raise SystemExit(f"SWH API is throttled; retry in {wait}s") from error
        raise
    path.write_bytes(data)
    return data


def get_json(url: str):
    return json.loads(fetch(url))


def object_meta(html: str) -> dict:
    match = re.search(
        r'<script id=swh_object_metadata type=application/json>(.*?)</script>',
        html, re.S)
    if not match:
        raise ValueError("no swh_object_metadata in browse page")
    return json.loads(match.group(1))


def directory_rows(html: str) -> list[dict]:
    """Parse the directory table.  The HTML omits optional </tr> tags."""
    out = []
    for part in html.split('<tr class="swh-directory-entry')[1:]:
        block = part.split("<tr", 1)[0]
        href = re.search(r'href="([^"]+)"', block)
        name = re.search(r"</i>\s*(.*?)\s*</a>", block, re.S)
        mode = re.search(r'<td class="d-none d-md-table-cell">([^<]*)', block)
        if not href or not name:
            continue
        out.append({"href": href.group(1), "name": name.group(1).strip(),
                    "mode": (mode.group(1).strip() if mode else "")})
    return out


def build_tree(root: str, path: str = "") -> str:
    key = (root, path)
    if key in DIR_CACHE:
        return DIR_CACHE[key]
    url = f"{BASE}/browse/directory/{root}/" + (f"?path={path}" if path else "")
    html = fetch(url).decode("utf-8", "replace")
    current = object_meta(html)["object_id"]
    lines = []
    for row in directory_rows(html):
        href = row["href"]
        if "/browse/content/sha1_git:" in href:
            blob = re.search(r"sha1_git:([0-9a-f]{40})", href).group(1)
            content = fetch(f"{BASE}/browse/content/sha1_git:{blob}/raw/")
            p = subprocess.run(["git", "-C", str(REPO), "hash-object", "-w", "--stdin"],
                               input=content, capture_output=True)
            if p.returncode:
                raise SystemExit(p.stderr.decode())
            got = p.stdout.decode().strip()
            if got != blob:
                raise SystemExit(f"blob mismatch {blob} -> {got}")
            mode = row["mode"]
            git_mode = ("120000" if mode.startswith("l")
                        else "100755" if "x" in mode else "100644")
            lines.append((git_mode, "blob", got, row["name"]))
        elif "?path=" in href:
            subpath = href.split("?path=", 1)[1]
            lines.append(("40000", "tree", build_tree(root, subpath), row["name"]))
        else:
            raise SystemExit(f"unknown directory row: {row}")
    lines.sort(key=lambda x: x[3])
    data = b"".join(f"{mode} {typ} {sha}\t{name}".encode() + b"\0"
                    for mode, typ, sha, name in lines)
    p = subprocess.run(["git", "-C", str(REPO), "mktree", "-z"],
                       input=data, capture_output=True)
    if p.returncode:
        raise SystemExit(p.stderr.decode())
    got = p.stdout.decode().strip()
    if got != current:
        raise SystemExit(f"tree mismatch {current} -> {got} at {path or '.'}")
    DIR_CACHE[key] = got
    return got


def write_commit(revision: dict) -> str:
    author = revision["author"]
    committer = revision.get("committer") or author
    date = datetime.fromisoformat(revision["date"].replace("Z", "+00:00"))
    epoch = int(date.timestamp())
    tz = date.strftime("%z")
    lines = [f"tree {revision['directory']}"]
    for parent in revision.get("parents") or []:
        lines.append(f"parent {parent['id']}")
    lines.append(f"author {author['fullname']} {epoch} {tz}")
    lines.append(f"committer {committer['fullname']} {epoch} {tz}")
    body = "\n".join(lines) + "\n\n" + revision["message"]
    p = subprocess.run(["git", "-C", str(REPO), "hash-object", "-t", "commit", "-w",
                        "--stdin"], input=body.encode(), capture_output=True)
    if p.returncode:
        raise SystemExit(p.stderr.decode())
    got = p.stdout.decode().strip()
    if got != revision["id"]:
        raise SystemExit(f"commit mismatch {revision['id']} -> {got}")
    return got


def revision_log(head: str) -> list[dict]:
    data = get_json(f"{BASE}/api/1/revision/{head}/log/?limit=1000")
    if not isinstance(data, list) or not data:
        raise SystemExit(f"no revision log for {head}")
    if data[0]["id"] != head:
        raise SystemExit(f"log starts at {data[0]['id']}, expected {head}")
    return data


def main() -> int:
    global REPO, CACHE
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("repo", type=Path,
                        help="target directory, e.g. repos/owner__name")
    parser.add_argument("--head", required=True,
                        help="SWH revision id (full 40-character SHA) to restore")
    parser.add_argument("--keep", type=int, default=20,
                        help="number of newest revisions to reconstruct (default 20)")
    parser.add_argument("--log", type=Path,
                        help="optional revision-log JSON; fetched from SWH if omitted")
    parser.add_argument("--cache", type=Path, default=CACHE,
                        help="directory for downloaded browse pages/content")
    parser.add_argument("--force", action="store_true",
                        help="replace an existing target directory")
    args = parser.parse_args()

    if args.keep < 1:
        parser.error("--keep must be positive")
    if args.repo.exists() and any(args.repo.iterdir()):
        if not args.force:
            parser.error(f"{args.repo} already exists; pass --force to replace it")
        shutil.rmtree(args.repo)

    CACHE = args.cache
    REPO = args.repo
    REPO.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(REPO)], check=True)
    subprocess.run(["git", "-C", str(REPO), "config", "user.name",
                    "SWH browse reconstruction"], check=True)
    subprocess.run(["git", "-C", str(REPO), "config", "user.email",
                    "swh@example.invalid"], check=True)

    if args.log:
        log = json.loads(args.log.read_text())
    else:
        log = revision_log(args.head)
    log = log[:args.keep]
    if log[0]["id"] != args.head:
        raise SystemExit(f"log starts at {log[0]['id']}, expected {args.head}")

    for revision in reversed(log):
        build_tree(revision["directory"])
        write_commit(revision)
        print(f"{revision['id'][:10]} {revision['date'][:10]} "
              f"{revision['message'].splitlines()[0][:60]}", flush=True)

    subprocess.run(["git", "-C", str(REPO), "update-ref", "refs/heads/master",
                    args.head], check=True)
    subprocess.run(["git", "-C", str(REPO), "symbolic-ref", "HEAD",
                    "refs/heads/master"], check=True)
    (REPO / ".git" / "shallow").write_text(log[-1]["id"] + "\n")
    subprocess.run(["git", "-C", str(REPO), "config", "remote.origin.url",
                    str(ROOT / "work" / "swh" / REPO.name)], check=True)
    subprocess.run(["git", "-C", str(REPO), "reset", "--hard", "-q", "HEAD"],
                   check=True)
    print(f"restored {len(log)} commits in {REPO} (HEAD {args.head})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
