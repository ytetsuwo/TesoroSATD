#!/usr/bin/env python3
"""Find the commit that first introduced each recorded comment body.

The provenance CSV records the last writer of the mapped comment lines. This
pass starts from that evidence and walks history backwards while the exact
comment body is still present, so a later edit is not mistaken for the first
introduction. The result is a separate file; provenance rows are not changed.

    python3 scripts/28_introduced.py
    python3 scripts/28_introduced.py --retry-truncated
    python3 scripts/28_introduced.py --max-walk 256
"""
from __future__ import annotations

import argparse
from collections import Counter, OrderedDict, deque
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
import csv
import hashlib
import json
import re
import subprocess
import sys

import astjava
from common import OUT, core, git, git_env, repo_dir, write_jsonl
from strict_verification import (
    Auditor, comment_groups, compact, dataset_body, parse_blame,
)

STATUSES = ("introduced", "ambiguous", "truncated", "unresolved",
            "not_confirmed", "unmapped")
LABELS = {
    "introduced": "導入コミット特定",
    "ambiguous": "複数の導入候補",
    "truncated": "履歴探索の打ち切り",
    "unresolved": "導入コミット未解決",
    "not_confirmed": "本文未確定のため対象外",
    "unmapped": "来歴なし",
}


def git_text(directory, *args):
    """Git output for source files that may not be valid UTF-8."""
    return subprocess.run(
        ["git", "-C", str(directory), "-c", "core.quotepath=false", *args],
        capture_output=True, text=True, errors="replace", env=git_env(),
    )


class Introducer:
    """Cached, line-aware history lookup for one audit run."""

    def __init__(self, maxsize=64, max_walk=64):
        self.auditor = Auditor(maxsize=maxsize)
        self.files = OrderedDict()
        self.meta = {}
        self.parents_cache = {}
        self.ancestor_cache = {}
        self.maxsize = maxsize
        self.max_walk = max_walk

    def groups_at(self, directory, sha, path):
        """Exact comment-body candidates in one file version, or None."""
        key = (str(directory), sha, path)
        if key in self.files:
            self.files.move_to_end(key)
            return self.files[key]
        result = git_text(directory, "show", f"{sha}:{path}")
        if result.returncode:
            value = None
        else:
            lines = result.stdout.split("\n")
            tree = astjava.parse("\n".join(lines).encode())
            value = {}
            for start, end, text in comment_groups(astjava.comments(tree), 1, len(lines), lines):
                value.setdefault(compact(text), []).append(start)
        self.files[key] = value
        if len(self.files) > self.maxsize:
            self.files.popitem(last=False)
        return value

    def present(self, directory, sha, path, wanted, hint=None, slack=64):
        """Closest exact or long-prefix body to the previous line.

        A dataset comment can be only the first line of a longer doc block.
        Short words are never accepted as partial matches.
        """
        groups = self.groups_at(directory, sha, path)
        if groups is None:
            return None
        wanted_key = compact(wanted)
        starts = []
        for text, positions in groups.items():
            if text == wanted_key or (len(wanted_key) >= 16 and wanted_key in text):
                starts.extend(positions)
        if not starts:
            return None
        if hint is None:
            return min(starts)
        nearest = min(starts, key=lambda start: abs(start - hint))
        return nearest if abs(nearest - hint) <= slack else None

    def file_exists(self, directory, sha, path):
        return git(directory, "cat-file", "-e", f"{sha}:{path}").returncode == 0

    def commit_meta(self, directory, sha):
        key = (str(directory), sha)
        if key not in self.meta:
            result = git(directory, "show", "-s", "--format=%H%x00%an%x00%ad%x00%ct",
                         "--date=short", sha)
            fields = result.stdout.strip().split("\0")
            self.meta[key] = {
                "sha": fields[0] if len(fields) > 0 else sha,
                "author": fields[1] if len(fields) > 1 else "",
                "date": fields[2] if len(fields) > 2 else "",
                "time": int(fields[3]) if len(fields) > 3 and fields[3].isdigit() else 0,
            }
        return self.meta[key]

    def parents(self, directory, sha):
        key = (str(directory), sha)
        if key not in self.parents_cache:
            result = git(directory, "rev-list", "--parents", "-n", "1", sha)
            fields = result.stdout.split()
            self.parents_cache[key] = fields[1:]
        return self.parents_cache[key]

    def is_ancestor(self, directory, older, newer):
        if older == newer:
            return False
        key = (str(directory), older, newer)
        if key not in self.ancestor_cache:
            self.ancestor_cache[key] = git(
                directory, "merge-base", "--is-ancestor", older, newer).returncode == 0
        return self.ancestor_cache[key]

    def latest(self, directory, states):
        commits = list(dict.fromkeys(state[0] for state in states))
        newest = [sha for sha in commits
                  if not any(sha != other and self.is_ancestor(directory, sha, other)
                             for other in commits)]
        if not newest:
            newest = commits
        return max(newest, key=lambda sha: self.commit_meta(directory, sha)["time"])

    def parent_path(self, directory, parent, child, path):
        """Keep following a renamed file; stop when the parent has no path."""
        if self.file_exists(directory, parent, path):
            return path
        result = git(directory, "diff-tree", "-r", "-M", "--name-status",
                     parent, child)
        for line in result.stdout.splitlines():
            fields = line.split("\t")
            if len(fields) >= 3 and fields[0].startswith("R") and fields[2] == path:
                return fields[1]
        return None

    def map_line(self, directory, parent, child, path, line):
        """Map an unchanged child line to its parent line; None if added."""
        result = git_text(directory, "diff", "-M", "--unified=0", "--no-color", parent, child)
        if result.returncode:
            return None
        offset = 0
        current = None
        for header in result.stdout.splitlines():
            if header.startswith("+++ b/"):
                current = header[6:]
                offset = 0
                continue
            if header.startswith("+++ /dev/null"):
                current = None
                continue
            if current != path:
                continue
            match = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
            if not match:
                continue
            old_start = int(match[1])
            old_count = int(match[2] or 1)
            new_start = int(match[3])
            new_count = int(match[4] or 1)
            if line < new_start:
                return line + offset
            if line < new_start + new_count:
                if old_count and line - new_start < old_count:
                    return old_start + (line - new_start)
                return None
            offset = (old_start + old_count) - (new_start + new_count)
        return line + offset

    def body_records(self, row):
        """Blame records for substantive comment lines, with their origin path."""
        lines, tree, fragments, nodes = self.auditor.source(row)
        first = int(row["comment_line"])
        last = first + int(row["n_comment_lines"]) - 1
        if not (1 <= first <= last <= len(lines)):
            raise ValueError("comment range outside source")
        revision = [] if row.get("at_head") == "True" else [row["snapshot_commit"]]
        path = row.get("path_snapshot") or row["path"]
        directory = repo_dir(row["repo"])
        _, coordinates = self.auditor.git_source(
            directory, path, revision[0] if revision else None)
        git_first, git_last = coordinates[first], coordinates[last]
        result = git(directory, "blame", "--line-porcelain", "-L",
                     f"{git_first},{git_last}", *revision, "--", path)
        if result.returncode:
            raise OSError(result.stderr.strip()[:160])
        records = parse_blame(result.stdout)
        if {record["line"] for record in records} != set(range(git_first, git_last + 1)):
            raise OSError("incomplete blame line coverage")
        reverse = {coordinates[i]: i for i in range(first, last + 1)}
        body = []
        for record in records:
            logical = reverse.get(record["line"])
            if logical is not None and core(" ".join(fragments.get(logical, []))):
                body.append(record)
        return body

    def version_state(self, row):
        directory = repo_dir(row["repo"])
        if row.get("at_head") == "True":
            result = git(directory, "rev-parse", "HEAD")
            if result.returncode:
                raise OSError(result.stderr.strip()[:160])
            sha = result.stdout.strip()
        else:
            sha = row["snapshot_commit"]
        return sha, row.get("path_snapshot") or row["path"]

    def evidence_states(self, row, audit, version):
        """Candidate versions from the strict audit, without a second blame."""
        path = row.get("path_at_commit") or row["path"]
        checks = (audit or {}).get("checks", {})
        origins = checks.get("commit", {}).get("origins", [])
        fallback_line = min((origin["line"] for origin in origins), default=None) \
            or int(row["comment_line"])
        states = [(row["commit_id"], path, fallback_line)]
        for origin in origins:
            if origin.get("sha") and origin.get("path"):
                states.append((origin["sha"], origin["path"], origin["line"]))
        for sha in checks.get("blame", {}).get("body_shas", []):
            states.append((sha, path, fallback_line))
        states.append((version[0], version[1], int(row["comment_line"])))
        return list(dict.fromkeys(states))

    def candidates(self, row, states, wanted, slack=64):
        directory = repo_dir(row["repo"])
        found, seen = [], set()
        for sha, path, hint in states:
            if (sha, path) in seen:
                continue
            seen.add((sha, path))
            matched = self.present(directory, sha, path, wanted, hint, slack)
            if matched is not None:
                found.append((sha, path, matched))
        return found

    def introduction(self, directory, start, path, hint, wanted):
        """Walk parents while the exact body remains; return the oldest roots."""
        queue = deque([(start, path, hint)])
        seen = {(start, path)}
        terminals, steps = [], 0
        while queue:
            if steps >= self.max_walk:
                return {"status": "truncated", "terminals": [], "steps": steps}
            commit, current_path, current_hint = queue.popleft()
            steps += 1
            following = []
            for parent in self.parents(directory, commit):
                parent_path = self.parent_path(directory, parent, commit, current_path)
                if not parent_path:
                    continue
                parent_line = self.map_line(directory, parent, commit, current_path, current_hint)
                if parent_line is None:
                    continue
                matched = self.present(directory, parent, parent_path, wanted,
                                       parent_line, 2)
                if matched is not None:
                    following.append((parent, parent_path, matched))
            if not following:
                terminals.append((commit, current_path, current_hint))
            else:
                for state in following:
                    if state[:2] not in seen:
                        seen.add(state[:2])
                        queue.append(state)
        terminals = list(dict.fromkeys(terminals))
        if not terminals:
            return {"status": "unresolved", "terminals": [], "steps": steps}
        terminals.sort(key=lambda state: self.commit_meta(directory, state[0])["time"])
        return {
            "status": "introduced" if len(terminals) == 1 else "ambiguous",
            "terminals": [{"sha": sha, "path": path, "line": line}
                          for sha, path, line in terminals],
            "steps": steps,
        }

    def pickaxe(self, directory, start, path, wanted):
        """Fast fallback for long walks: find the oldest commit changing a line."""
        lines = [line.strip() for line in wanted.splitlines() if len(line.strip()) >= 8]
        if not lines:
            return None
        commits = set()
        for needle in sorted(set(lines), key=len, reverse=True):
            result = git(directory, "log", "--format=%H", "--reverse",
                         "-S", needle, start, "--", path)
            if result.returncode == 0:
                commits.update(result.stdout.splitlines())
        for sha in sorted(commits, key=lambda item: self.commit_meta(directory, item)["time"]):
            matched = self.present(directory, sha, path, wanted, None)
            if matched is not None:
                return {"sha": sha, "path": path, "line": matched}
        return None

    def analyse(self, row, audit):
        if not row.get("commit_id") or not row.get("comment_line"):
            return {"status": "unmapped", "reason": "no recorded provenance"}
        if not audit or audit.get("checks", {}).get("text", {}).get("status") != "confirmed":
            return {"status": "not_confirmed", "reason": "strict text check is not confirmed"}
        wanted = dataset_body(row["commenttext"])
        try:
            version = self.version_state(row)
            states = self.evidence_states(row, audit, version)
            candidates = self.candidates(row, states, wanted)
            if not candidates:
                records = self.body_records(row)
                if not records:
                    return {"status": "unresolved", "reason": "no substantive blame records"}
                states = [(record["sha"], record["path"], record["line"]) for record in records]
                states.append((version[0], version[1], int(row["comment_line"])))
                candidates = self.candidates(row, states, wanted)
        except (OSError, ValueError, KeyError) as error:
            return {"status": "unresolved", "reason": str(error)}
        if not candidates:
            return {"status": "unresolved", "reason": "comment body absent from all candidate versions"}
        directory = repo_dir(row["repo"])
        body_shas = set((audit or {}).get("checks", {}).get("blame", {}).get("body_shas", []))
        body_shas.add(row["commit_id"])
        body_candidates = [state for state in candidates if state[:2] != version]
        pool = body_candidates if body_candidates else candidates
        start = self.latest(directory, pool)
        start_path, start_hint = next((path, hint) for sha, path, hint in pool if sha == start)
        walked = self.introduction(directory, start, start_path, start_hint, wanted)
        basis = "blame" if start in body_shas else "version"
        if walked["status"] == "truncated":
            fallback = self.pickaxe(directory, version[0], version[1], wanted)
            if fallback:
                walked = {"status": "introduced", "terminals": [fallback],
                          "steps": walked["steps"], "fallback": "pickaxe"}
                basis = "pickaxe"
        meta = self.commit_meta(directory, walked["terminals"][0]["sha"]) \
            if walked["terminals"] else {}
        return {
            **walked,
            "introduced_in": meta.get("sha", ""),
            "introduced_date": meta.get("date", ""),
            "introduced_author": meta.get("author", ""),
            "basis": basis,
            "candidate_count": len(candidates),
        }


def audit_map():
    path = OUT / "strict_audit.jsonl"
    if not path.exists():
        raise SystemExit("missing out/strict_audit.jsonl; run 27_strict_audit.py first")
    with open(path, encoding="utf-8") as file:
        return {(row["function_id"], row["comment_id"]): row
                for row in map(json.loads, file)}


_WORKER = None


def init_worker(max_walk):
    global _WORKER
    _WORKER = Introducer(max_walk=max_walk)


def analyse_worker(item):
    row, audit = item
    try:
        return row, _WORKER.analyse(row, audit)
    except Exception as error:  # one broken repository must not stop the audit
        return row, {"status": "unresolved", "reason": str(error)[:160]}


def retry_truncated(introducer, rows, previous):
    """Re-search only the rows whose parent walk hit the limit, via pickaxe."""
    records = []
    retried = 0
    for index, row in enumerate(rows, 1):
        key = (row["function_id"], row["comment_id"])
        record = dict(previous.get(key) or {})
        if record.get("status") == "truncated":
            retried += 1
            try:
                directory = repo_dir(row["repo"])
                version = introducer.version_state(row)
                wanted = dataset_body(row["commenttext"])
                found = introducer.pickaxe(directory, version[0], version[1], wanted)
                if found:
                    meta = introducer.commit_meta(directory, found["sha"])
                    record.update({
                        "status": "introduced", "terminals": [found], "steps": 0,
                        "introduced_in": meta["sha"], "introduced_date": meta["date"],
                        "introduced_author": meta["author"], "basis": "pickaxe",
                        "fallback": "pickaxe",
                    })
                    record.pop("reason", None)
            except Exception as error:
                record["retry_error"] = str(error)[:160]
            if retried % 20 == 0:
                print(f"retried {retried} truncated rows", flush=True)
        records.append(record)
    print(f"retried {retried} truncated rows", flush=True)
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-walk", type=int, default=128,
                        help="maximum commits to inspect while walking parents")
    parser.add_argument("--jobs", type=int, default=6,
                        help="parallel worker processes (each keeps its own caches)")
    parser.add_argument("--retry-truncated", action="store_true",
                        help="re-search only truncated rows with the pickaxe history scan")
    parser.add_argument("--fail-on", default="",
                        help="comma-separated statuses that cause exit 1")
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    rejected = set(filter(None, args.fail_on.split(",")))
    if rejected - set(STATUSES):
        parser.error("unknown failure status")

    with open(OUT / "tesoro_provenance.csv", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    audits = audit_map()
    if args.retry_truncated:
        previous_path = OUT / "introduced.jsonl"
        if not previous_path.exists():
            raise SystemExit("missing out/introduced.jsonl; run 28_introduced.py first")
        previous = {}
        for line in previous_path.read_text().splitlines():
            record = json.loads(line)
            previous[(record["function_id"], record["comment_id"])] = record
        records = retry_truncated(Introducer(max_walk=args.max_walk), rows, previous)
    else:
        jobs = max(1, args.jobs)
        local = Introducer(max_walk=args.max_walk) if jobs == 1 else None

        def sequential():
            for row in rows:
                key = (row["function_id"], row["comment_id"])
                try:
                    yield row, local.analyse(row, audits.get(key))
                except Exception as error:  # one broken repository must not stop the audit
                    yield row, {"status": "unresolved", "reason": str(error)[:160]}

        executor = ProcessPoolExecutor(max_workers=jobs, initializer=init_worker,
                                       initargs=(args.max_walk,)) if jobs > 1 else None
        with (executor if executor is not None else nullcontext()) as pool:
            if pool is not None:
                results = pool.map(analyse_worker, (
                    (row, audits.get((row["function_id"], row["comment_id"]))) for row in rows
                ), chunksize=8)
            else:
                results = sequential()
            records = []
            for index, (row, result) in enumerate(results, 1):
                records.append({
                    "function_id": row["function_id"],
                    "comment_id": row["comment_id"],
                    "projectname": row["projectname"],
                    "repo": row["repo"],
                    "path": row.get("path_snapshot") or row["path"],
                    "comment_line": row["comment_line"],
                    "commenttext": row["commenttext"],
                    "recorded_commit": row["commit_id"],
                    **result,
                })
                if index % 500 == 0:
                    print(f"analysed {index}/{len(rows)}", flush=True)

    counts = Counter(record["status"] for record in records)
    identified = [record for record in records if record["status"] == "introduced"]
    same = sum(record["introduced_in"] == record["recorded_commit"] for record in identified)
    summary = {
        "schema_version": 1,
        "rows": len(records),
        "mapped": sum(bool(row.get("commit_id")) for row in rows),
        "provenance_sha256": hashlib.sha256(
            (OUT / "tesoro_provenance.csv").read_bytes()).hexdigest(),
        "counts": dict(counts),
        "identified": len(identified),
        "same_as_recorded_commit": same,
        "different_from_recorded_commit": len(identified) - same,
        "unique_introduced_commits": len({record["introduced_in"] for record in identified}),
        "max_walk": args.max_walk,
        "definition": ("the commit that introduced the current exact comment body, by walking "
                       "parents while the body remains present; not necessarily the first SATD "
                       "appearance if the wording changed earlier or the text was copied"),
    }

    fields = ["function_id", "comment_id", "projectname", "repo", "path",
              "comment_line", "commenttext", "introduced_in", "introduced_short",
              "introduced_url", "introduced_date", "introduced_author", "basis",
              "status", "recorded_commit"]
    with open(OUT / "tesoro_introduced.csv", "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            sha = record.get("introduced_in", "")
            writer.writerow({
                **{field: record.get(field, "") for field in fields},
                "introduced_short": sha[:7],
                "introduced_url": f"https://github.com/{record['repo']}/commit/{sha}" if sha else "",
            })
    write_jsonl(OUT / "introduced.jsonl", records)
    (OUT / "introduced_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    lines = [
        "# SATD 導入コミット",
        "",
        f"対象: {len(records)} コメント（来歴付き {summary['mapped']}）",
        "",
        "| 状態 | 件数 |",
        "| --- | ---: |",
    ]
    lines += [f"| {LABELS[status]} | {counts[status]} |" for status in STATUSES]
    lines += [
        "",
        f"導入コミットを特定: {len(identified)} 件。うち記録済み `commit_id` と同一 "
        f"{same} 件、異なる {len(identified) - same} 件。",
        "",
        "`introduced_in` は、現在のコメント本文が最初に現れたコミットである。",
        "本文が後から書き換えられている場合は、書き換え後の本文を導入したコミットであり、",
        "SATD コメント全体の最初の導入コミットとは限らない。",
        "`recorded_commit` は従来の来歴（本文行の最終更新者）で、変更していない。",
    ]
    (OUT / "introduced_summary.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(any(counts[status] for status in rejected))


if __name__ == "__main__":
    sys.exit(main())
