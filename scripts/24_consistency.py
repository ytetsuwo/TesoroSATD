#!/usr/bin/env python3
"""Cross-check the exported files, the dataset and the README numbers."""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter

from common import OUT, REPOS

problems = []


def check(cond, msg):
    if not cond:
        problems.append(msg)


def rows(name):
    with open(OUT / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


prov = rows("tesoro_provenance.csv")
msr = rows("tesoro_msr16.csv")
vers = rows("tesoro_msr16_versioned.csv")
loc = [json.loads(x) for x in open(OUT / "functions_located.jsonl") if x.strip()]
code = [json.loads(x) for x in open(OUT.parent / "tesoro/data/tesoro/tesoro_code.json")]
comm = [json.loads(x) for x in open(OUT.parent / "tesoro/data/tesoro/tesoro_comment.json")]

check(len(prov) == len(comm) == len(msr) == len(vers) == 4981,
      f"row counts: prov {len(prov)} msr {len(msr)} vers {len(vers)} data {len(comm)}")
check(len(code) == 1255, f"functions: {len(code)}")
check({str(c["id"]) for c in code} == {r["function_id"] for r in prov},
      "function ids in provenance differ from the dataset")

got = {(r["function_id"], r["comment_id"]) for r in prov}
check(len(got) == len(prov), "duplicate (function, comment) rows")

withc = sum(1 for r in prov if r["commit_id"])
st = Counter(r["status"] for r in prov)
check(withc == sum(1 for r in loc for c in r.get("comments") or [] if c.get("lines")),
      "commit rows differ from mapped comments in the JSONL")

locmap = {r["id"]: r for r in loc}
fn_status = {r["function_id"]: r["status"] for r in prov}
check(Counter(fn_status.values()) == Counter(r["status"] for r in loc),
      f"status counts differ: {Counter(fn_status.values())} vs {Counter(r['status'] for r in loc)}")

# at_head rows must have a HEAD path, snapshot rows a commit
for r in prov:
    if r["commit_id"] and r["at_head"] == "True":
        check(r["path"] and (REPOS / r["repo"].replace("/", "__") / r["path"]).exists()
              or (REPOS / r["repo"]).exists(), f"missing file for {r['function_id']}")
    if r["commit_id"] and r["at_head"] != "True":
        check(re.match(r"^[0-9a-f]{7,40}\^?$", r["snapshot_commit"] or ""),
              f"snapshot missing for {r['function_id']}")

# versioned names carry the commit when there is one
bad_v = [r for r in vers if not re.match(r"^[^\s@]+/[^\s@]+(@[0-9a-f]{7,40})?$", r["projectname"])]
check(not bad_v, f"versioned projectname malformed: {len(bad_v)}")
unversioned = sum(1 for r in vers if "@" not in r["projectname"])
print(f"versioned rows without a commit: {unversioned}")
check({r["classification"] for r in msr} <= {
    "WITHOUT_CLASSIFICATION", "DESIGN", "IMPLEMENTATION", "DEFECT", "DOCUMENTATION", "TEST"},
    "unexpected labels in MSR16")

# README numbers agree with the summary
readme = (OUT.parent / "README.md").read_text()
summary = (OUT / "summary.md").read_text()
m = re.search(r"コメント (\d+)/(\d+) \(([\d.]+)%\)", readme)
check(m and int(m.group(1)) == withc, f"README coverage {m.group(0) if m else None} != {withc}")
m2 = re.search(r"コメント: \d+ 件中 (\d+) 件", summary)
check(m2 and int(m2.group(1)) == withc, "summary.md coverage differs")

# manifest vs directories
man = rows("repo_manifest.csv")
dirs = {p.name for p in REPOS.iterdir()}
check({r["repo"] for r in man} == dirs, "manifest does not match repos/")

print(f"rows: {len(prov)}  with commit: {withc}  status: {dict(st)}")
print(f"manifest: {len(man)}  dirs: {len(dirs)}")
if problems:
    print("\nFAIL")
    for p in problems[:15]:
        print("  -", p)
    sys.exit(1)
print("\nOK: dataset, exports, JSONL, README and manifest agree")
