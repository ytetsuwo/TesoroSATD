"""Independent, conservative evidence for each comment's version and location.

Legacy matching scores are not proof. Missing evidence is reported explicitly;
historical and relocated comments are inspected at their own snapshot.
"""
from collections import OrderedDict
import csv
from difflib import SequenceMatcher
import re
import unicodedata

import astjava
from common import OUT, core, git, load_tesoro, repo_dir
from source_files import SourceFiles

STATUSES = ("confirmed", "review", "mismatch", "unverifiable", "unmapped")
HEADER = re.compile(r"^([0-9a-f]{40}) (\d+) (\d+)(?: \d+)?$")


def verdict(status, reason, **evidence):
    return {"status": status, "reason": reason, **evidence}


def body(text):
    """Remove outer comment delimiters, never punctuation inside the prose."""
    lines = []
    for line in text.splitlines():
        if not line.lstrip().startswith("//"):
            line = re.sub(r"\*/\s*$", "", line)
        line = re.sub(r"^\s*(?://+|/\*+|\*+)\s?", "", line)
        lines.append(line.strip())
    return "\n".join(lines).strip()


def compact(text):
    return "".join(unicodedata.normalize("NFKC", text).split())


def dataset_body(text):
    tree = astjava.parse(text.encode())
    nodes = astjava.comments(tree)
    return "\n".join(body(node["text"]) for node in nodes).strip() if nodes else body(text)


def comment_groups(nodes, first, last, lines=None):
    """Comment bodies that can form one dataset comment inside a line range.

    Individual nodes are always candidates. Adjacent line comments (including
    blank lines) and several comments on one line are also joined when their
    ordered text exactly equals the recorded body.
    """
    ordered = sorted((node for node in nodes if first <= node["start"] and node["end"] <= last),
                     key=lambda node: (node["start"], node["end"]))
    groups = []
    by_line = {}
    for node in ordered:
        by_line.setdefault(node["start"], []).append(body(node["text"]))
    for start, parts in by_line.items():
        if len(parts) > 1:
            groups.append((start, start, "\n".join(parts)))
    for index, node in enumerate(ordered):
        text = body(node["text"])
        groups.append((node["start"], node["end"], text))
        end, parts = node["end"], [text]
        for following in ordered[index + 1:]:
            if following["start"] <= end:
                continue
            gap = range(end + 1, following["start"])
            if lines is not None and any(lines[i - 1].strip() for i in gap):
                break
            if following["start"] == end + 1 or lines is not None:
                parts.append(body(following["text"]))
                end = following["end"]
            else:
                break
        if len(parts) > 1:
            groups.append((node["start"], end, "\n".join(parts)))
    return groups


def parse_blame(output):
    """Keep original line, current line, filename and SHA together per line."""
    records, current = [], None
    for line in output.split("\n"):
        header = HEADER.match(line)
        if header:
            current = {"sha": header[1], "original_line": int(header[2]),
                       "line": int(header[3])}
        elif current is not None and line.startswith("filename "):
            current["path"] = line[9:]
        elif current is not None and line.startswith("\t"):
            current["text"] = line[1:]
            records.append(current)
            current = None
    return records


def aggregate(checks):
    statuses = {check["status"] for check in checks.values()}
    for status in ("unmapped", "mismatch", "unverifiable", "review"):
        if status in statuses:
            return status
    return "confirmed"


def audit_rows():
    """Include unmapped rows; derive the target name from the dataset, not a guess at HEAD."""
    funcs, comments = load_tesoro()
    expected = {}
    for fid, function in funcs.items():
        candidates = comments.get(fid, [])
        representative = max(candidates, key=lambda c: len(c.get("code") or "")) if candidates else {}
        code = representative.get("code") or function.get("original_code") or ""
        methods = astjava.methods(astjava.parse(("class Dataset {\n" + code + "\n}").encode()))
        if methods:
            expected[str(fid)] = min(methods, key=lambda m: (m["depth"], m["start"]))["name"]
    with open(OUT / "tesoro_provenance.csv", encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        if row["function_id"] in expected:
            row["expected_method"] = expected[row["function_id"]]
    return rows


class Auditor:
    """Bounded source/AST caches; construct a fresh auditor for each run."""

    def __init__(self, maxsize=32):
        self.sources = SourceFiles(maxsize=maxsize)
        self.maxsize = maxsize
        self.trees = OrderedDict()
        self.git_sources = OrderedDict()

    def git_source(self, directory, path, revision):
        """Git counts LF lines; CSV/AST positions use Python's splitlines()."""
        key = (directory, path, revision)
        if key not in self.git_sources:
            if revision is None:
                raw = (directory / path).read_text(errors="replace")
            else:
                result = git(directory, "show", f"{revision}:{path}")
                if result.returncode:
                    raise OSError(result.stderr.strip()[:160])
                raw = result.stdout
            logical_to_git, physical = [None], 1
            for line in raw.splitlines(keepends=True):
                logical_to_git.append(physical)
                physical += line.count("\n")
            self.git_sources[key] = (raw.split("\n"), logical_to_git)
            if len(self.git_sources) > self.maxsize:
                self.git_sources.popitem(last=False)
        self.git_sources.move_to_end(key)
        return self.git_sources[key]

    def source(self, row):
        directory = repo_dir(row["repo"])
        path = row.get("path_snapshot") or row["path"]
        revision = None if row.get("at_head") == "True" else row.get("snapshot_commit")
        if row.get("at_head") != "True" and not revision:
            raise OSError("snapshot revision missing")
        key = (directory, path, revision)
        if key not in self.trees:
            lines = self.sources.read(directory, path, revision)
            tree = astjava.parse("\n".join(lines).encode())
            fragments = {}
            nodes = astjava.comments(tree)
            for comment in nodes:
                for offset, text in enumerate(comment["text"].splitlines()):
                    fragments.setdefault(comment["start"] + offset, []).append(body(text))
            self.trees[key] = (lines, tree, fragments, nodes)
            if len(self.trees) > self.maxsize:
                self.trees.popitem(last=False)
        self.trees.move_to_end(key)
        return self.trees[key]

    def syntax(self, row):
        try:
            lines, tree, fragments, nodes = self.source(row)
            first = int(row["comment_line"])
            last = first + int(row["n_comment_lines"]) - 1
        except (OSError, ValueError, KeyError) as error:
            missing = verdict("unverifiable", str(error))
            return {"ast": missing, "text": dict(missing)}
        if not (1 <= first <= last <= len(lines)):
            failed = verdict("mismatch", "comment range outside source", first=first, last=last)
            return {"ast": failed, "text": dict(failed)}
        selected = range(first, last + 1)
        non_comment = [i for i in selected if lines[i - 1].strip() and i not in fragments]
        actual = "\n".join(" ".join(fragments.get(i, [])) for i in selected).strip()
        wanted = dataset_body(row["commenttext"])
        a = compact(wanted)
        groups = comment_groups(nodes, first, last, lines)
        exact = next(((start, end, text) for start, end, text in groups
                      if a and compact(text) == a), None)
        empty_exact = next(((start, end, text) for start, end, text in groups
                            if not a and not compact(text)), None)
        prefix = next(((start, end, text) for start, end, text in groups
                       if a and core(wanted) and compact(text).startswith(a)), None)
        incomplete_empty = next((node for node in nodes
                                 if not a and node["start"] <= first <= last <= node["end"]), None)
        combined_exact = bool(a) and compact(actual) == a
        combined_empty = not a and not compact(actual)
        source_body = exact[2] if exact else prefix[2] if prefix else actual
        b = compact(source_body)
        ratio = SequenceMatcher(None, a, b, autojunk=False).ratio()
        # Compare AST node groups: one line can hold several comments, adjacent
        # line comments can form one dataset comment.
        if exact or combined_exact:
            text_status = "review" if not core(wanted) else "confirmed"
            text_reason = "symbol matches; context still requires review" if text_status == "review" \
                else "complete ordered comment body matches"
        elif empty_exact or combined_empty:
            text_status, text_reason = "review", "empty comment matches; context still requires review"
        elif incomplete_empty is not None:
            text_status, text_reason = "review", "empty documentation comment extends beyond recorded span"
        elif prefix:
            text_status, text_reason = "review", "recorded body is a prefix of a longer comment"
        elif a and b and core(wanted) and ratio >= 0.80:
            text_status, text_reason = "review", "wording differs; not full confirmation"
        else:
            text_status, text_reason = "mismatch", "comment body differs"
        text_check = verdict(text_status, text_reason, similarity=round(ratio, 6),
                             dataset_body=wanted, source_body=source_body,
                             matched=exact[2] if exact else empty_exact[2] if empty_exact else None,
                             differences=[op for op in SequenceMatcher(None, a, b, autojunk=False).get_opcodes()
                                          if op[0] != "equal"])
        methods = astjava.methods(tree)
        owners = [m for m in methods if m["start"] <= first <= last <= m["end"]]
        owner = max(owners, key=lambda m: m["depth"]) if owners else None
        where = "body_or_declaration" if owner else "outside"
        if owner is None:
            for method in methods:
                docs = astjava.doc_block(tree, method)
                if docs and docs[0]["start"] <= first <= last <= docs[-1]["end"]:
                    owner, where = method, "above"
                    break
        errors = astjava.errors(tree)
        expected = row.get("expected_method")
        if not expected and row.get("at_head") == "True" and (row.get("path_snapshot") or row["path"]) == row["path"]:
            target = astjava.locate_method(tree, int(row.get("start_line") or 0), slack=0)
            expected = target["name"] if target else None
        if non_comment or not any(i in fragments for i in selected):
            status, reason = "mismatch", "mapped nonblank lines are not comment nodes"
        elif errors:
            status, reason = "review", "syntax errors prevent unconditional scope confirmation"
        elif owner is None or not expected:
            status, reason = "review", "target method membership not established"
        elif owner["name"] != expected:
            status, reason = "review", "comment belongs to another method"
        else:
            status, reason = "confirmed", "comment nodes belong to the expected method or its documentation"
        ast_check = verdict(status, reason, owner=owner, expected_method=expected, where=where,
                            non_comment_lines=non_comment, parse_errors=len(errors), first=first, last=last,
                            snapshot_path=row.get("path_snapshot") or row["path"],
                            snapshot_commit=row.get("snapshot_commit") or "HEAD")
        return {"ast": ast_check, "text": text_check}

    def attribution(self, row):
        """Validate substantive lines, not a SHA that occurs only on a delimiter."""
        try:
            lines, tree, fragments, nodes = self.source(row)
            first = int(row["comment_line"])
            last = first + int(row["n_comment_lines"]) - 1
            if not 1 <= first <= last <= len(lines):
                raise ValueError("comment range outside source")
            revision = [] if row.get("at_head") == "True" else [row["snapshot_commit"]]
            path = row.get("path_snapshot") or row["path"]
            _, coordinates = self.git_source(repo_dir(row["repo"]), path,
                                              revision[0] if revision else None)
            git_first, git_last = coordinates[first], coordinates[last]
            result = git(repo_dir(row["repo"]), "blame", "--line-porcelain", "-L", f"{git_first},{git_last}",
                         *revision, "--", path)
            if result.returncode:
                raise OSError(result.stderr.strip()[:160])
            records = parse_blame(result.stdout)
            if {r["line"] for r in records} != set(range(git_first, git_last + 1)):
                raise OSError("incomplete blame line coverage")
        except (OSError, ValueError, KeyError) as error:
            check = verdict("unverifiable", str(error))
            return {"blame": check, "commit": dict(check)}
        substantive_lines = {coordinates[i] for i in range(first, last + 1)
                             if core(" ".join(fragments.get(i, [])))}
        comment_lines = {coordinates[i] for i in range(first, last + 1) if i in fragments}
        substantive = [r for r in records if r["line"] in substantive_lines]
        evidence = substantive or [r for r in records if r["line"] in comment_lines]
        claimed = [r for r in evidence if r["sha"] == row["commit_id"]]
        shas = sorted({r["sha"] for r in evidence})
        if not claimed:
            blame_check = verdict("mismatch", "claimed SHA does not own comment-body lines", body_shas=shas)
        elif evidence[0]["sha"] != row["commit_id"] or len(shas) > 1 or not substantive:
            blame_check = verdict("review", "multiple body authors, non-primary SHA, or symbols only",
                                  body_shas=shas, attributed_lines=claimed)
        else:
            blame_check = verdict("confirmed", "claimed SHA owns every substantive comment line",
                                  body_shas=shas, attributed_lines=claimed)
        failures, origins = [], []
        try:
            for record in claimed:
                origin_path = record.get("path")
                if not origin_path:
                    raise OSError("blame origin filename missing")
                recorded_path = row.get("path_at_commit") or row["path"]
                if origin_path != recorded_path:
                    failures.append({"line": record["original_line"], "path": origin_path,
                                     "recorded_path": recorded_path, "reason": "recorded origin path differs from blame"})
                origin, _ = self.git_source(repo_dir(row["repo"]), origin_path, record["sha"])
                original = record["original_line"]
                if not 1 <= original <= len(origin) or origin[original - 1] != record["text"]:
                    failures.append({"line": original, "path": origin_path})
                origins.append({"line": original, "path": origin_path, "sha": record["sha"]})
        except OSError as error:
            return {"blame": blame_check, "commit": verdict("unverifiable", str(error))}
        if not claimed or failures:
            commit_check = verdict("mismatch", "comment absent at its blamed original location",
                                   failures=failures, origins=origins)
        else:
            commit_check = verdict("confirmed", "comment-body lines exist at blamed original locations",
                                   origins=origins, definition="last writer of recorded comment-body lines, not necessarily first introduction")
        return {"blame": blame_check, "commit": commit_check}

    def verify(self, row, full=True):
        if not row.get("commit_id") or not row.get("comment_line"):
            return {"function_id": row["function_id"], "comment_id": row["comment_id"],
                    "status": "unmapped", "checks": {}, "match": row.get("match", "")}
        checks = self.syntax(row)
        if full:
            checks.update(self.attribution(row))
        return {"function_id": row["function_id"], "comment_id": row["comment_id"],
                "repo": row["repo"], "match": row["match"], "status": aggregate(checks), "checks": checks}
