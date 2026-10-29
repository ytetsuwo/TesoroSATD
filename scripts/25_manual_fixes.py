#!/usr/bin/env python3
"""Deterministic re-application of the comment mappings that were fixed by hand.

Each entry names the revision, file and lines that were found during the
investigation; the commit is recomputed with `git blame`, so the result does not
depend on the order of the earlier passes.

    python3 scripts/25_manual_fixes.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime

from common import OUT, repo_dir

# (function_id, comment_id, revision or None for HEAD, path, first, last, match)
FIXES = [
    (15634, 14, "e0aa47caf", "src/main/java/quanta/actpub/ActPubService.java", 238, 238, "history"),
    (16350, 0, "e5521533530e9815e25d21d26518b7efc9a69ac4",
     "src/main/java/uk/gov/hmcts/reform/iacaseapi/domain/handlers/postsubmit/ReviewTimeExtensionConfirmation.java",
     47, 47, "history"),
    (8889, 3, "28391cbe0ff8^",
     "services/core/java/com/android/server/wm/WindowManagerService.java", 3728, 3728, "history"),
    (10081, 1, "2a93b24", "sources/pmchess/logic/Board.java", 207, 207, "history"),
    (14698, 0, "9ad589a97c3",
     "model/model-impl/src/main/java/com/evolveum/midpoint/model/impl/lens/AssignmentCollector.java",
     156, 156, "history"),
    (19832, 0, None,
     "MainProject/use-3.0.7/src/main/org/tzi/use/uml/sys/events/AttributeAssignedEvent.java",
     61, 64, "reshaped"),
    (19833, 0, None,
     "MainProject/use-3.0.7/src/main/org/tzi/use/uml/sys/soil/MAttributeAssignmentStatement.java",
     125, 129, "relocated"),
    (18158, 0, None,
     "vendor/sqlite/src/java/com/couchbase/lite/internal/database/util/DatabaseUtils.java",
     778, 817, "relocated"),
    (20776, 0, None, "src/org/sosy_lab/cpachecker/cpa/invariants/CompoundBitVectorInterval.java",
     1491, 1506, "reshaped"),
    (15634, 0, "cce32e091", "src/main/java/quanta/actpub/ActPubService.java", 116, 116, "history"),
    (34588, 0, "adeca0a5b", "purpleLib/src/main/java/com/purplehillsbooks/json/JSONObject.java",
     1581, 1581, "history"),
    (20778, 0, None, "src/org/sosy_lab/cpachecker/cpa/invariants/CompoundBitVectorInterval.java",
     1585, 1601, "reshaped"),
]


def blame(d, rev, path, ln):
    p = subprocess.run(["git", "-C", str(d), "-c", "core.quotepath=false",
                        "blame", "--line-porcelain", "-L", f"{ln},{ln}"] +
                       ([rev] if rev else []) + ["--", path],
                       capture_output=True, text=True)
    out = {}
    for line in p.stdout.splitlines():
        if re.match(r"^[0-9a-f]{40} ", line):
            out["sha"] = line.split()[0]
        elif line.startswith("author "):
            out["author"] = line[7:]
        elif line.startswith("author-time "):
            out["date"] = datetime.fromtimestamp(int(line[12:])).strftime("%Y-%m-%d")
        elif line.startswith("summary "):
            out["summary"] = line[8:]
    return out


def main() -> int:
    path = OUT / "functions_located.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    by_id = {r["id"]: r for r in rows}
    n = 0
    for fid, cid, rev, fpath, first, last, kind in FIXES:
        loc = by_id.get(fid)
        if not loc:
            continue
        d = repo_dir(loc["repo"])
        info = blame(d, rev, fpath, first)
        for c in loc.get("comments") or []:
            if c["comment_id"] != cid:
                continue
            c.update({"lines": list(range(first, last + 1)), "match": kind,
                      "commit": info.get("sha"), "author": info.get("author"),
                      "author_time": info.get("date"), "summary": info.get("summary"),
                      "path_snapshot": fpath, "path_at": fpath})
            if rev:
                c["at_commit"] = rev
            n += 1
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)
    print(f"applied {n} manual mappings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
