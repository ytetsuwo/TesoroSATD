"""Probe token-level similarity between a dataset function and a file at HEAD."""
import json, re, sys
from difflib import SequenceMatcher
from pathlib import Path
sys.path.insert(0, "scripts")
from common import load_tesoro, repo_dir

BLOCK = re.compile(r"/\*.*?\*/", re.S)
LINE = re.compile(r"//[^\n]*")
TOK = re.compile(r'"[^"]*"|[A-Za-z_$][\w$]*|\d+|\S')


def strip_comments(s):
    return LINE.sub("", BLOCK.sub("", s))


def toks(s):
    return TOK.findall(strip_comments(s))


def ref_of(fid):
    funcs, comments = load_tesoro()
    rows = [r for r in comments.get(fid, []) if (r.get("code") or "").strip()]
    rep = max(rows, key=lambda r: len(r.get("code") or ""))
    return rep["code"]


def best(path, ref, span=3):
    rt = toks(ref)
    lines = path.read_text(errors="replace").splitlines()
    txt = "\n".join(lines)
    ft = toks(txt)
    # token index -> line
    bestr, bestoff = 0, 0
    n = len(rt)
    step = max(1, n // 4)
    for off in range(0, max(1, len(ft) - n + 1), step):
        r = SequenceMatcher(None, rt, ft[off:off + n], autojunk=False).ratio()
        if r > bestr:
            bestr, bestoff = r, off
    for off in range(max(0, bestoff - step), min(len(ft), bestoff + step + 1)):
        r = SequenceMatcher(None, rt, ft[off:off + n], autojunk=False).ratio()
        if r > bestr:
            bestr, bestoff = r, off
    return bestr, bestoff


if __name__ == "__main__":
    fid, rel = int(sys.argv[1]), sys.argv[2]
    funcs, _ = load_tesoro()
    ref = ref_of(fid)
    p = repo_dir(funcs[fid]["repo"]) / rel
    r, off = best(p, ref)
    print(f"{fid} {funcs[fid]['repo']} {rel}: token ratio {r:.3f} (ref {len(toks(ref))} tokens)")
