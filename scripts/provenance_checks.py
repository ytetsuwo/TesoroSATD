"""Read-only provenance checks, independent of the verification CLI."""
from common import alnum, core, git, norm, repo_dir, similarity
from comment_matching import cbody as ctext
from source_files import SourceFiles


def content(row, sources=None):
    """Read the HEAD or snapshot file; cache only within an explicit run."""
    directory = repo_dir(row["repo"])
    path = row.get("path_snapshot") or row["path"]
    revision = None if row.get("at_head") == "True" else row["snapshot_commit"]
    return (sources if sources is not None else SourceFiles()).read(directory, path, revision)


def _cores(lines):
    return [value for value in map(core, lines) if value]


def check_text(row, sources=None):
    """Apply the existing rule for each match kind, without tightening it."""
    try:
        lines = content(row, sources)
    except OSError as error:
        return f"read: {error}"
    start = int(row["comment_line"]) - 1
    got = lines[start:start + int(row["n_comment_lines"])]
    want = [line for line in row["commenttext"].splitlines() if norm(line)]
    if not got:
        return "line out of range"
    got_core, want_core = _cores(got), _cores(want)
    kind = row["match"]
    if kind == "position":
        return None if not any(core(ctext(line)) for line in got) else "position: text present"
    if kind == "reshaped":
        a, b = alnum(" ".join(want)), alnum(" ".join(got))
        return None if a and (a in b or similarity(" ".join(want), " ".join(got)) >= 0.9) \
            else "reshaped but differs"
    if kind == "relocated":
        combined = " ".join(got_core)
        return None if all(value in combined for value in want_core) else "relocated but differs"
    if kind == "exact":
        return None if got_core == want_core else "exact but differs"
    if kind == "history" and got_core == want_core:
        return None
    if kind in ("history", "fuzzy"):
        ratio = similarity(" ".join(want), " ".join(ctext(line) for line in got))
        minimum = 0.85 if kind == "history" else 0.80
        return None if ratio >= minimum else f"{kind} {ratio:.2f}"
    combined = " ".join(got_core)
    return None if any(value in combined for value in want_core) else "structural, no shared line"


def check_blame(row):
    directory = repo_dir(row["repo"])
    start = int(row["comment_line"])
    end = start + int(row["n_comment_lines"]) - 1
    revision = [] if row.get("at_head") == "True" else [row["snapshot_commit"]]
    path = row.get("path_snapshot") or row["path"]
    result = git(directory, "blame", "--line-porcelain", "-L", f"{start},{end}",
                 *revision, "--", path)
    if result.returncode != 0:
        return f"blame failed: {result.stderr.strip()[:120]}"
    shas = {parts[0] for line in result.stdout.splitlines()
            if (parts := line.split()) and len(parts[0]) == 40}
    return None if row["commit_id"] in shas else f"blame {[sha[:8] for sha in shas]}"


def check_commit(row, sources=None):
    directory = repo_dir(row["repo"])
    sha, path = row["commit_id"], row["path_at_commit"] or row["path"]
    try:
        lines = (sources if sources is not None else SourceFiles()).read(directory, path, sha)
    except OSError as error:
        return f"show failed: {error}"
    if row["match"] in ("fuzzy", "position"):
        # Existing policy: the blamed commit needs the file, not the old wording.
        return None if any(line.strip() for line in lines) else "empty file at commit"
    want = _cores(row["commenttext"].splitlines())
    have = " ".join(_cores(lines))
    return None if any(value in have for value in want) else "comment absent in that commit"


CHECKS = (("text", check_text), ("blame", check_blame), ("show", check_commit))
