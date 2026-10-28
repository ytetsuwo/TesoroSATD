"""Inspect the 8 weak_match functions: is the code really absent or just reformatted?"""
import json, sys
from pathlib import Path
sys.path.insert(0, "scripts")
from common import load_tesoro, repo_dir

funcs, comments = load_tesoro()
targets = [10862, 11774, 12044, 15634, 19374, 22578, 25248, 30244]
for fid in targets:
    f = funcs[fid]
    rows = [r for r in comments.get(fid, []) if (r.get("code") or "").strip()]
    rep = max(rows, key=lambda r: len(r.get("code") or "")) if rows else None
    code = (rep or {}).get("code") or f.get("original_code") or ""
    print("=" * 100)
    print(fid, f["repo"], "label", f.get("label"), "n_comments", len(comments.get(fid, [])))
    print("--- repository fields:", {k: v for k, v in f.items() if k not in ("original_code",)})
    print("--- code ---")
    print("\n".join(code.splitlines()[:40]))
