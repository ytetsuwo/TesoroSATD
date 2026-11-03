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
    Auditor, body, comment_groups, compact, dataset_body, parse_blame,
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
DATASET_STATUSES = ("current", "historical", "candidate", "ambiguous",
                    "near_exact", "prefix", "context", "not_informative",
                    "not_found", "unmapped", "error")
DATASET_LABELS = {
    "current": "現在本文と同一",
    "historical": "履歴で完全一致",
    "candidate": "履歴候補（親探索打ち切り）",
    "ambiguous": "履歴候補（複数系統）",
    "near_exact": "近い候補（本文差あり）",
    "prefix": "接頭辞（現在本文の一部）",
    "context": "周辺コードから推定（記号・空）",
    "not_informative": "短語のため対象外",
    "not_found": "本文が履歴に見つからない",
    "unmapped": "来歴なし",
    "error": "探索エラー",
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

    def exact_start(self, directory, sha, path, wanted):
        """Return a line only when the whole recorded body is present exactly."""
        groups = self.groups_at(directory, sha, path)
        if groups is None:
            return None
        starts = groups.get(compact(wanted), [])
        return min(starts) if starts else None

    def pickaxe(self, directory, start, path, wanted, raw=""):
        """Fast fallback for long walks: find the oldest commit changing a line."""
        lines = [line.strip() for line in wanted.splitlines() if len(line.strip()) >= 4]
        lines += [line.strip() for line in raw.splitlines() if len(line.strip()) >= 4]
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
            if matched is None:
                matched = self.raw_present(directory, sha, path, wanted)
            if matched is not None:
                return {"sha": sha, "path": path, "line": matched}
        history = git(directory, "log", "--full-history", "--format=%H", "--reverse",
                      start, "--", path)
        if history.returncode == 0:
            for sha in history.stdout.splitlines():
                matched = self.present(directory, sha, path, wanted, None)
                if matched is None:
                    matched = self.raw_present(directory, sha, path, wanted)
                if matched is not None:
                    return {"sha": sha, "path": path, "line": matched}
        return None

    def raw_present(self, directory, sha, path, wanted):
        """Line-based fallback for comments spanning several separate lines."""
        result = git_text(directory, "show", f"{sha}:{path}")
        if result.returncode:
            return None
        wanted_key = compact(wanted)
        parts, start = [], None
        for number, line in enumerate(result.stdout.split("\n"), 1):
            stripped = line.lstrip()
            if not stripped:
                continue
            if not (stripped.startswith("//") or stripped.startswith("*")
                    or stripped.startswith("/*")):
                parts, start = [], None
                continue
            text = compact(body(line))
            if not text:
                continue
            if not parts:
                start = number
            parts.append(text)
            joined = "".join(parts)
            if joined == wanted_key or (len(wanted_key) >= 4 and wanted_key in joined):
                return start
        return None

    def dataset_commit(self, directory, terminal, status, basis, similarity):
        meta = self.commit_meta(directory, terminal["sha"])
        result = {
            "dataset_status": status,
            "dataset_basis": basis,
            "dataset_introduced_in": meta.get("sha", ""),
            "dataset_introduced_date": meta.get("date", ""),
            "dataset_introduced_author": meta.get("author", ""),
            "dataset_line": terminal.get("line", ""),
            "dataset_path": terminal.get("path", ""),
        }
        if similarity is not None:
            result["dataset_similarity"] = round(similarity, 6)
        return result

    def source_lines(self, directory, sha, path):
        result = git_text(directory, "show", f"{sha}:{path}")
        return result.stdout.split("\n") if result.returncode == 0 else None

    def comment_span(self, lines, line, count):
        """First and last line of the comment node(s) around a mapped range."""
        tree = astjava.parse("\n".join(lines).encode())
        nodes = [node for node in astjava.comments(tree)
                 if node["end"] >= line and node["start"] <= line + count - 1]
        if not nodes:
            return None
        return min(node["start"] for node in nodes), max(node["end"] for node in nodes)

    def context_signature(self, lines, first, last):
        """The comment lines plus the nearest code lines around them."""
        def normalize(value):
            return re.sub(r"\s+", " ", value.strip())

        def is_code(value):
            text = value.strip()
            return bool(text) and not text.startswith(("//", "/*", "*"))

        full = tuple(normalize(lines[index - 1]) for index in range(first, last + 1))
        previous = next((normalize(lines[index - 1])
                         for index in range(first - 1, 0, -1)
                         if is_code(lines[index - 1])), None)
        following = next((normalize(lines[index - 1])
                          for index in range(last + 1, len(lines) + 1)
                          if is_code(lines[index - 1])), None)
        return full, previous, following

    def context_introduction(self, directory, start, path, first, last,
                             signature, max_walk):
        """Walk parents while the comment and its code surroundings remain."""
        queue = deque([(start, path, first, last)])
        seen = {(start, path)}
        terminals, steps = [], 0
        while queue:
            if steps >= max_walk:
                return {"status": "truncated", "terminals": [], "steps": steps}
            sha, current_path, current_first, current_last = queue.popleft()
            steps += 1
            following = []
            for parent in self.parents(directory, sha):
                parent_path = self.parent_path(directory, parent, sha, current_path)
                if not parent_path:
                    continue
                parent_first = self.map_line(
                    directory, parent, sha, current_path, current_first)
                parent_last = self.map_line(
                    directory, parent, sha, current_path, current_last)
                if parent_first is None or parent_last is None:
                    continue
                lines = self.source_lines(directory, parent, parent_path)
                if lines is None or not (1 <= parent_first <= parent_last <= len(lines)):
                    continue
                if self.comment_span(lines, parent_first, parent_last) is None:
                    continue
                if self.context_signature(lines, parent_first, parent_last) != signature:
                    continue
                following.append((parent, parent_path, parent_first, parent_last))
            if not following:
                terminals.append((sha, current_path, current_first))
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

    def context_analysis(self, row):
        """Proxy introduction for an empty or symbol-only comment.

        The text itself cannot identify an origin, so the comment node and its
        nearest code lines are followed through history. The result is labelled
        `context` and never treated as an exact body match.
        """
        directory = repo_dir(row["repo"])
        try:
            version = self.version_state(row)
            lines = self.source_lines(directory, version[0], version[1])
        except Exception as error:
            return {"dataset_status": "error", "dataset_basis": "none",
                    "dataset_note": str(error)[:160]}
        if lines is None:
            return {"dataset_status": "not_found", "dataset_basis": "none",
                    "dataset_note": "source file is not available"}
        line = int(row["comment_line"])
        count = int(row["n_comment_lines"])
        span = self.comment_span(lines, line, count)
        if span is None:
            return {"dataset_status": "not_found", "dataset_basis": "none",
                    "dataset_note": "no comment node at the mapped location"}
        first, last = span
        signature = self.context_signature(lines, first, last)
        walked = self.context_introduction(
            directory, version[0], version[1], first, last, signature, self.max_walk)
        if walked["status"] == "truncated":
            walked = self.context_introduction(
                directory, version[0], version[1], first, last, signature,
                max(self.max_walk, 2048))
        terminals = walked.get("terminals") or []
        if not terminals:
            return {"dataset_status": "not_found", "dataset_basis": "none",
                    "dataset_note": "no stable code context in the available history"}
        terminal = terminals[0]
        if walked["status"] == "ambiguous":
            terminal = min(terminals, key=lambda item: self.commit_meta(
                directory, item["sha"])["time"])
        result = self.dataset_commit(directory, terminal, "context",
                                     "context-lineage", None)
        body_text = dataset_body(row["commenttext"]).strip()
        if body_text:
            result["dataset_note"] = (
                "記号のみの本文のため、本文ではなく周辺コードの履歴から推定した代理値（確定ではない）")
        else:
            result["dataset_note"] = (
                "空コメントのため、本文ではなく周辺コードの履歴から推定した代理値（確定ではない）")
        if walked["status"] == "ambiguous":
            result["dataset_terminals"] = terminals
            result["dataset_note"] += "。独立した系統が複数"
        return result

    def dataset_analysis(self, row, audit):
        """Identify the dataset body separately from the current file body.

        An exact historical body is `historical`; long ambiguities and near
        matches stay labelled as candidates. Symbol and empty bodies use a
        separate `context` proxy, and short generic words are not promoted.
        """
        wanted = dataset_body(row["commenttext"])
        core_body = core(wanted)
        if not core_body:
            return self.context_analysis(row)
        if len(core_body) < 8:
            return {"dataset_status": "not_informative", "dataset_basis": "none",
                    "dataset_note": "short generic body"}
        directory = repo_dir(row["repo"])
        text = (audit or {}).get("checks", {}).get("text", {})
        source = text.get("source_body", "")
        similarity = float(text.get("similarity") or 0)
        try:
            version = self.version_state(row)
            source_key = compact(source) if source else ""
            wanted_key = compact(wanted)
            is_prefix = text.get("reason") == "recorded body is a prefix of a longer comment"
            if is_prefix and source_key.startswith(wanted_key) and len(source_key) > len(wanted_key):
                source_found = self.pickaxe(directory, version[0], version[1],
                                            source, source)
                if source_found:
                    source_walk = self.introduction(
                        directory, source_found["sha"], source_found["path"],
                        source_found["line"], source)
                    terminals = source_walk.get("terminals") or []
                    if terminals:
                        result = self.dataset_commit(directory, terminals[0], "prefix",
                                                     "source-prefix", similarity)
                        result["dataset_note"] = (
                            "dataset body is a prefix of the source comment")
                        return result
            found = self.pickaxe(directory, version[0], version[1], wanted,
                                 row["commenttext"])
        except Exception as error:
            return {"dataset_status": "error", "dataset_basis": "none",
                    "dataset_note": str(error)[:160]}
        if found:
            try:
                walked = self.introduction(directory, found["sha"], found["path"],
                                           found["line"], wanted)
            except Exception:
                walked = {"status": "unresolved", "terminals": [], "steps": 0}
            terminals = walked.get("terminals") or []
            if walked["status"] == "introduced" and terminals:
                return self.dataset_commit(directory, terminals[0], "historical",
                                           "history-exact", 1.0)
            if walked["status"] == "ambiguous" and terminals:
                oldest = min(terminals, key=lambda item: self.commit_meta(
                    directory, item["sha"])["time"])
                result = self.dataset_commit(directory, oldest, "ambiguous",
                                             "history-ambiguous", 1.0)
                result["dataset_terminals"] = terminals
                result["dataset_note"] = "multiple independent exact-body branches"
                return result
            if walked["status"] == "truncated":
                result = self.dataset_commit(directory, found, "candidate",
                                             "history-exact-candidate", 1.0)
                result["dataset_note"] = (
                    f"exact body present; parent walk truncated at {walked.get('steps', 0)}")
                return result
        if source and len(core(source)) >= 8 and similarity >= 0.95:
            try:
                source_found = self.pickaxe(directory, version[0], version[1], source, source)
                if source_found:
                    source_walk = self.introduction(
                        directory, source_found["sha"], source_found["path"],
                        source_found["line"], source)
                    terminals = source_walk.get("terminals") or []
                    if terminals:
                        result = self.dataset_commit(directory, terminals[0], "near_exact",
                                                     "near-exact", similarity)
                        result["dataset_note"] = (
                            "dataset body differs slightly from the exact source body")
                        return result
            except Exception:
                pass
        return {"dataset_status": "not_found", "dataset_basis": "none",
                "dataset_note": "no exact or near-exact body in the available history"}

    def analyse(self, row, audit):
        if not row.get("commit_id") or not row.get("comment_line"):
            return {"status": "unmapped", "reason": "no recorded provenance",
                    "dataset_status": "unmapped", "dataset_basis": "none"}
        if not audit or audit.get("checks", {}).get("text", {}).get("status") != "confirmed":
            result = self.dataset_analysis(row, audit)
            return {"status": "not_confirmed",
                    "reason": "strict text check is not confirmed", **result}
        wanted = dataset_body(row["commenttext"])
        try:
            version = self.version_state(row)
            states = self.evidence_states(row, audit, version)
            candidates = self.candidates(row, states, wanted)
            if not candidates:
                records = self.body_records(row)
                if not records:
                    result = self.dataset_analysis(row, audit)
                    return {"status": "unresolved", "reason": "no substantive blame records",
                            **result}
                states = [(record["sha"], record["path"], record["line"]) for record in records]
                states.append((version[0], version[1], int(row["comment_line"])))
                candidates = self.candidates(row, states, wanted)
        except (OSError, ValueError, KeyError) as error:
            result = self.dataset_analysis(row, audit)
            return {"status": "unresolved", "reason": str(error), **result}
        if not candidates:
            result = self.dataset_analysis(row, audit)
            return {"status": "unresolved",
                    "reason": "comment body absent from all candidate versions", **result}
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
            fallback = self.pickaxe(directory, version[0], version[1], wanted,
                                     row["commenttext"])
            if fallback:
                walked = {"status": "introduced", "terminals": [fallback],
                          "steps": walked["steps"], "fallback": "pickaxe"}
                basis = "pickaxe"
        meta = self.commit_meta(directory, walked["terminals"][0]["sha"]) \
            if walked["terminals"] else {}
        current = {
            **walked,
            "introduced_in": meta.get("sha", ""),
            "introduced_date": meta.get("date", ""),
            "introduced_author": meta.get("author", ""),
            "basis": basis,
            "candidate_count": len(candidates),
        }
        if walked["status"] == "introduced" and meta:
            current.update({
                "dataset_status": "current",
                "dataset_basis": "current",
                "dataset_introduced_in": meta.get("sha", ""),
                "dataset_introduced_date": meta.get("date", ""),
                "dataset_introduced_author": meta.get("author", ""),
                "dataset_similarity": 1.0,
                "dataset_line": walked["terminals"][0].get("line", ""),
                "dataset_path": walked["terminals"][0].get("path", ""),
            })
        else:
            current.update(self.dataset_analysis(row, audit))
        return current


def resolve_ambiguous(introducer, rows, previous):
    """Use exact-body history and first-parent lineage to collapse candidates."""
    records = []
    resolved = 0
    first_parent = {}
    for row in rows:
        key = (row["function_id"], row["comment_id"])
        record = dict(previous.get(key) or {})
        if record.get("status") == "ambiguous":
            try:
                directory = repo_dir(row["repo"])
                version = introducer.version_state(row)
                wanted = dataset_body(row["commenttext"])
                needles = [line.strip() for line in wanted.splitlines() if len(line.strip()) >= 4]
                needles += [line.strip() for line in row["commenttext"].splitlines()
                            if len(line.strip()) >= 4]
                commits = set()
                for needle in sorted(set(needles), key=len, reverse=True):
                    result = git(directory, "log", "--format=%H", "--reverse",
                                 "-S", needle, version[0], "--", version[1])
                    if result.returncode == 0:
                        commits.update(result.stdout.splitlines())
                exact = []
                for sha in sorted(commits, key=lambda item: introducer.commit_meta(directory, item)["time"]):
                    line = introducer.exact_start(directory, sha, version[1], wanted)
                    if line is not None:
                        exact.append((sha, line))
                if directory not in first_parent:
                    first_parent[directory] = set(git(
                        directory, "rev-list", "--first-parent", "HEAD").stdout.splitlines())
                preferred = [item for item in exact if item[0] in first_parent[directory]]
                choice = preferred[0] if len(preferred) == 1 else exact[0] if len(exact) == 1 else None
                if choice is None:
                    terminal_preferred = [
                        (t["sha"], t["line"]) for t in record.get("terminals", [])
                        if t["sha"] in first_parent[directory]
                        and introducer.exact_start(directory, t["sha"], t["path"], wanted) is not None
                    ]
                    if len(terminal_preferred) == 1:
                        choice = terminal_preferred[0]
                if choice is None:
                    recorded_line = introducer.exact_start(
                        directory, row["commit_id"], version[1], wanted)
                    if recorded_line is not None:
                        choice = (row["commit_id"], recorded_line)
                if choice:
                    sha, line = choice
                    meta = introducer.commit_meta(directory, sha)
                    record.update({
                        "status": "introduced",
                        "terminals": [{"sha": sha, "path": version[1], "line": line}],
                        "steps": 0,
                        "introduced_in": meta["sha"],
                        "introduced_date": meta["date"],
                        "introduced_author": meta["author"],
                        "basis": "exact-first-parent" if preferred else "exact",
                        "fallback": "ambiguous-resolution",
                        "dataset_status": "current", "dataset_basis": "current",
                        "dataset_introduced_in": meta["sha"],
                        "dataset_introduced_date": meta["date"],
                        "dataset_introduced_author": meta["author"],
                        "dataset_similarity": 1.0,
                        "dataset_line": line,
                        "dataset_path": version[1],
                    })
                    resolved += 1
            except Exception as error:
                record["retry_error"] = str(error)[:160]
        records.append(record)
    print(f"resolved {resolved} ambiguous rows", flush=True)
    return records


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
        return row, {"status": "unresolved", "reason": str(error)[:160],
                     "dataset_status": "error", "dataset_basis": "none"}


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
                found = introducer.pickaxe(directory, version[0], version[1], wanted,
                                           row["commenttext"])
                if found:
                    meta = introducer.commit_meta(directory, found["sha"])
                    record.update({
                        "status": "introduced", "terminals": [found], "steps": 0,
                        "introduced_in": meta["sha"], "introduced_date": meta["date"],
                        "introduced_author": meta["author"], "basis": "pickaxe",
                        "fallback": "pickaxe",
                        "dataset_status": "current", "dataset_basis": "current",
                        "dataset_introduced_in": meta["sha"],
                        "dataset_introduced_date": meta["date"],
                        "dataset_introduced_author": meta["author"],
                        "dataset_similarity": 1.0,
                        "dataset_line": found.get("line", ""),
                        "dataset_path": found.get("path", ""),
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
    parser.add_argument("--retry-ambiguous", action="store_true",
                        help="resolve ambiguous rows using exact-body history and first-parent lineage")
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
    if args.retry_truncated or args.retry_ambiguous:
        previous_path = OUT / "introduced.jsonl"
        if not previous_path.exists():
            raise SystemExit("missing out/introduced.jsonl; run 28_introduced.py first")
        previous = {}
        for line in previous_path.read_text().splitlines():
            record = json.loads(line)
            previous[(record["function_id"], record["comment_id"])] = record
        records = []
        for row in rows:
            key = (row["function_id"], row["comment_id"])
            records.append(dict(previous.get(key) or {}))
        introducer = Introducer(max_walk=args.max_walk)
        if args.retry_truncated:
            records = retry_truncated(introducer, rows,
                                      {(record.get("function_id"), record.get("comment_id")): record
                                       for record in records})
        if args.retry_ambiguous:
            records = resolve_ambiguous(introducer, rows,
                                        {(record.get("function_id"), record.get("comment_id")): record
                                         for record in records})
    else:
        jobs = max(1, args.jobs)
        local = Introducer(max_walk=args.max_walk) if jobs == 1 else None

        def sequential():
            for row in rows:
                key = (row["function_id"], row["comment_id"])
                try:
                    yield row, local.analyse(row, audits.get(key))
                except Exception as error:  # one broken repository must not stop the audit
                    yield row, {"status": "unresolved", "reason": str(error)[:160],
                                "dataset_status": "error", "dataset_basis": "none"}

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
    dataset_counts = Counter(record.get("dataset_status", "not_found") for record in records)
    identified = [record for record in records if record["status"] == "introduced"]
    dataset_identified = [record for record in records if record.get("dataset_introduced_in")]
    dataset_exact = sum(record.get("dataset_status") in {"current", "historical"}
                        for record in records)
    same = sum(record["introduced_in"] == record["recorded_commit"] for record in identified)
    summary = {
        "schema_version": 2,
        "rows": len(records),
        "mapped": sum(bool(row.get("commit_id")) for row in rows),
        "provenance_sha256": hashlib.sha256(
            (OUT / "tesoro_provenance.csv").read_bytes()).hexdigest(),
        "counts": dict(counts),
        "identified": len(identified),
        "same_as_recorded_commit": same,
        "different_from_recorded_commit": len(identified) - same,
        "unique_introduced_commits": len({record["introduced_in"] for record in identified}),
        "dataset": {
            "counts": dict(dataset_counts),
            "identified": len(dataset_identified),
            "exact": dataset_exact,
            "definition": ("the commit found for the dataset comment body; `current` is the "
                           "same body as the current file, `historical` is an exact older body, "
                           "and candidate/near_exact are not confirmations"),
        },
        "max_walk": args.max_walk,
        "definition": ("the commit that introduced the current exact comment body, by walking "
                       "parents while the body remains present; not necessarily the first SATD "
                       "appearance if the wording changed earlier or the text was copied"),
    }

    fields = ["function_id", "comment_id", "projectname", "repo", "path",
              "comment_line", "commenttext", "introduced_in", "introduced_short",
              "introduced_url", "introduced_date", "introduced_author", "basis",
              "status", "recorded_commit", "dataset_introduced_in",
              "dataset_introduced_short", "dataset_introduced_url",
              "dataset_introduced_date", "dataset_introduced_author",
              "dataset_basis", "dataset_status", "dataset_similarity",
              "dataset_path", "dataset_line", "dataset_note"]
    with open(OUT / "tesoro_introduced.csv", "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            sha = record.get("introduced_in", "")
            dataset_sha = record.get("dataset_introduced_in", "")
            writer.writerow({
                **{field: record.get(field, "") for field in fields},
                "introduced_short": sha[:7],
                "introduced_url": f"https://github.com/{record['repo']}/commit/{sha}" if sha else "",
                "dataset_introduced_short": dataset_sha[:7],
                "dataset_introduced_url": (
                    f"https://github.com/{record['repo']}/commit/{dataset_sha}"
                    if dataset_sha else ""),
            })
    write_jsonl(OUT / "introduced.jsonl", records)
    (OUT / "introduced_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    lines = [
        "# SATD 導入コミット",
        "",
        f"対象: {len(records)} コメント（来歴付き {summary['mapped']}）",
        "",
        "## 現在の本文",
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
        "",
        "## データセット本文",
        "",
        "現在のファイル本文と一致しない場合に、dataset 側の本文を履歴から別に探した結果。",
        "",
        "| 状態 | 件数 |",
        "| --- | ---: |",
    ]
    lines += [f"| {DATASET_LABELS.get(status, status)} | {dataset_counts[status]} |"
              for status in DATASET_STATUSES]
    lines += [
        "",
        f"`dataset_introduced_in` を付けた件数: {len(dataset_identified)} "
        f"（うち現在本文と同一または履歴で完全一致 {dataset_exact}）。",
        "`historical` は dataset 本文が過去の版に完全一致したもの、`candidate` と",
        "`ambiguous` は候補、`near_exact` は本文に差がある参考候補、`context` は",
        "記号・空コメントの周辺コードから推定した代理値である。",
    ]
    (OUT / "introduced_summary.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return int(any(counts[status] for status in rejected))


if __name__ == "__main__":
    sys.exit(main())
