"""Shared helpers for the Tesoro provenance pipeline.

The pipeline maps every Tesoro function back to (repo, file, commit) so that
`git blame` can be run on the exact lines that carry the SATD comment.
"""
from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from git_auth import git_env

ROOT = Path(__file__).resolve().parent.parent
TESORO = ROOT / "tesoro"
DATA = TESORO / "data"
REPOS = ROOT / "repos"
WORK = ROOT / "work"
OUT = ROOT / "out"

for d in (REPOS, WORK, OUT):
    d.mkdir(parents=True, exist_ok=True)


def read_jsonl(path):
    """Read a .json file that actually holds one JSON object per line."""
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def write_jsonl(path, rows):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(path)


def load_tesoro():
    """Return (functions, comments) keyed by function id."""
    funcs = {r["id"]: r for r in read_jsonl(DATA / "tesoro" / "tesoro_code.json")}
    comments = {}
    for r in read_jsonl(DATA / "tesoro" / "tesoro_comment.json"):
        comments.setdefault(r["id"], []).append(r)
    return funcs, comments


def repo_dir(repo: str) -> Path:
    return REPOS / repo.replace("/", "__")


def run(cmd, cwd, timeout=900, input_=None):
    return subprocess.run(cmd, cwd=str(cwd), input=input_, capture_output=True,
                          text=True, errors="replace", timeout=timeout, env=git_env())


def head_of(d: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], d, timeout=120).stdout.strip()


def git(path: Path, *args, check=False, timeout=1800):
    return subprocess.run(
        ["git", "-C", str(path), "-c", "core.quotepath=false", *args],
        capture_output=True, text=True, check=check, timeout=timeout, env=git_env(),
    )


COMMENT_MARK = re.compile(r"^\s*(//+|/\*+|\*+|#)\s?")
WS = re.compile(r"\s+")


def strip_marker(line: str) -> str:
    return COMMENT_MARK.sub("", line).strip()


def norm(s: str) -> str:
    return WS.sub(" ", s).strip()


# Commentary is written in many scripts, so "letters and digits" must mean the
# Unicode categories, not [0-9a-z]: Chinese, Cyrillic and accented Latin text
# otherwise collapses to an empty string and can never be matched.
NONALNUM = re.compile(r"[\W_]+", re.UNICODE)
WORD = re.compile(r"[^\W_]+", re.UNICODE)


def alnum(s: str) -> str:
    return NONALNUM.sub("", unicodedata.normalize("NFKC", s).lower())


def tokens(s: str) -> list[str]:
    return WORD.findall(unicodedata.normalize("NFKC", s).lower())


def similarity(a: str, b: str) -> float:
    """Word overlap for spaced prose, character overlap for CJK and friends."""
    ta, tb = tokens(a), tokens(b)
    if len(ta) >= 8 and len(tb) >= 8:
        return SequenceMatcher(None, ta, tb, autojunk=False).ratio()
    return SequenceMatcher(None, alnum(a), alnum(b), autojunk=False).ratio()


def core(s: str) -> str:
    """Comment text without markers, whitespace and punctuation."""
    return alnum(strip_marker(s))


def mclass(s: str) -> str:
    t = s.strip()
    if t.startswith("//"):
        return "//"
    if t.startswith("/*"):
        return "/*"
    if t.startswith("*"):
        return "*"
    return ""


def body_end(lines, start: int):
    """Closing-brace line (1-based) of the function whose declaration is at `start`."""
    depth, opened, in_block = 0, False, False
    for i in range(start - 1, len(lines)):
        text = lines[i]
        if in_block:
            if "*/" in text:
                in_block = False
                text = text.split("*/", 1)[1]
            else:
                continue
        text = re.sub(r"/\*.*?\*/", "", text)
        if "/*" in text:
            in_block = True
            text = text.split("/*", 1)[0]
        text = re.sub(r'"(\\.|[^"\\])*"', '""', text)
        text = re.sub(r"'(\\.|[^'\\])*'", "''", text)
        text = re.sub(r"//.*", "", text)
        depth += text.count("{") - text.count("}")
        opened = opened or "{" in text
        if opened and depth <= 0:
            return i + 1
    return None


def doc_start(lines, start: int) -> int:
    """First line (1-based) of the comment block directly above `start`."""
    i = start - 2                      # 0-based line above the declaration
    while i >= 0:
        t = lines[i].strip()
        if not t or t.startswith("@") or t.startswith("//") or t.startswith("*") \
                or t.startswith("/*") or t.endswith("*/"):
            i -= 1
            continue
        break
    return i + 2                       # 1-based first line of that block
