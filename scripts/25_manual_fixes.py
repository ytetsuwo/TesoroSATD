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

from common import OUT, core, load_tesoro, repo_dir

# (function_id, comment_id, revision or None for HEAD, path, first, last, match)
FIXES = [
    (9775, 2, None, "java8-voxxed/src/main/java/victor/training/java8/voxxed/UncheckedAndLambdas.java", 28, 28, "exact"),
    (15390, 12, None, "src/data/RecipeArgParser.java", 512, 512, "structural"),
    (24503, 18, None, "TeamCode/src/main/java/org/firstinspires/ftc/teamcode/Auton_CoachBrian_FAST3.java", 162, 162, "structural"),
    (24503, 21, None, "TeamCode/src/main/java/org/firstinspires/ftc/teamcode/Auton_CoachBrian_FAST3.java", 174, 174, "structural"),
    (24503, 23, None, "TeamCode/src/main/java/org/firstinspires/ftc/teamcode/Auton_CoachBrian_FAST3.java", 182, 182, "structural"),
    (24503, 27, None, "TeamCode/src/main/java/org/firstinspires/ftc/teamcode/Auton_CoachBrian_FAST3.java", 192, 192, "structural"),
    (24503, 29, None, "TeamCode/src/main/java/org/firstinspires/ftc/teamcode/Auton_CoachBrian_FAST3.java", 204, 204, "structural"),
    (34583, 23, None, "src/main/java/jline/console/ConsoleReader.java", 2738, 2743, "structural"),
    (10532, 7, None, "app/controllers/OnlineController.java", 120, 122, "structural"),
    (18865, 3, None, "src/main/java/cat/calidos/morfeu/model/injection/CellModule.java", 263, 265, "structural"),
    (2538, 5, None, "src/main/java/com/xmlcalabash/util/FoFOP.java", 242, 247, "structural"),
    (2538, 6, None, "src/main/java/com/xmlcalabash/util/FoFOP.java", 288, 293, "structural"),
    (1032, 29, None, "src/server/maps/pvp/PvPLibrary.java", 277, 277, "structural"),
    (1032, 30, None, "src/server/maps/pvp/PvPLibrary.java", 283, 283, "structural"),
    (1032, 31, None, "src/server/maps/pvp/PvPLibrary.java", 289, 289, "structural"),
    (1032, 32, None, "src/server/maps/pvp/PvPLibrary.java", 295, 295, "structural"),
    (1032, 67, None, "src/server/maps/pvp/PvPLibrary.java", 580, 580, "structural"),
    (15149, 318, None, "src/main/java/com/progenia/sigdep01_01/dialogs/EditerRubriqueDialog.java", 1856, 1860, "structural"),
    (15149, 320, None, "src/main/java/com/progenia/sigdep01_01/dialogs/EditerRubriqueDialog.java", 1865, 1869, "structural"),
    (15149, 321, None, "src/main/java/com/progenia/sigdep01_01/dialogs/EditerRubriqueDialog.java", 1873, 1877, "structural"),
    (15149, 322, None, "src/main/java/com/progenia/sigdep01_01/dialogs/EditerRubriqueDialog.java", 1881, 1885, "structural"),
    (15149, 323, None, "src/main/java/com/progenia/sigdep01_01/dialogs/EditerRubriqueDialog.java", 1889, 1893, "structural"),
    (24980, 4, None, "usage/src/com/cloud/usage/UsageManagerImpl.java", 379, 379, "structural"),
    (15703, 45, None, "apps/Email/src/com/android/email/MessagingController.java", 885, 894, "structural"),
    (31506, 0, None,
     "software/camod/test/unit/gov/nih/nci/camod/domain/AvailabilityTest.java",
     124, 126, "relocated"),
    (31507, 0, None,
     "software/camod/test/unit/gov/nih/nci/camod/domain/AvailabilityTest.java",
     134, 136, "relocated"),
    (16008, 0, None, "core/src/de/sengerts/tictactoe/model/players/AIPlayer.java",
     29, 34, "relocated"),
    (16008, 1, None, "core/src/de/sengerts/tictactoe/model/players/AIPlayer.java",
     37, 37, "relocated"),
    (784, 0, None, "reactor-core/src/test/java/reactor/core/publisher/FluxPublishOnTest.java",
     988, 988, "relocated"),
    (2992, 53, "18693e88c", "src/org/opengts/war/track/taglib/TrackTag.java", 897, 897, "structural"),
    (2992, 55, "18693e88c", "src/org/opengts/war/track/taglib/TrackTag.java", 912, 912, "structural"),
    (15927, 106, None, "java/sage/miniclient/QuartzGFXCMD.java", 1306, 1306, "structural"),
    (19831, 0, None,
     "MainProject/use-3.0.7/src/main/org/tzi/use/uml/sys/soil/MOperationCallStatement.java",
     82, 86, "structural"),
    (15634, 14, "e0aa47caf", "src/main/java/quanta/actpub/ActPubService.java", 238, 238, "history"),
    (16350, 0, "436c81aa7f353c2cc26c3ce866cd7f750883a826",
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
    (15634, 0, "cce32e091", "src/main/java/quanta/actpub/ActPubService.java", 115, 132, "history"),
    (34588, 0, "adeca0a5b", "purpleLib/src/main/java/com/purplehillsbooks/json/JSONObject.java",
     1580, 1610, "history"),
    (19585, 0, "f019e0c2f94e08e66572956735f3f5c6969db5d4",
     "weblayer/browser/java/org/chromium/weblayer_private/payments/WebLayerPaymentRequestService.java", 63, 64, "history"),
    (20104, 0, "611d21bc6f867af25637c37ed062c6fbc022d70c",
     "chrome/android/java/src/org/chromium/chrome/browser/bookmarks/BookmarkUtils.java", 235, 242, "history"),
    (24102, 0, "d48d2b1639774af47043bf653ccfb15fd972005b",
     "chrome/android/javatests/src/org/chromium/chrome/browser/vr/VrBrowserNativeUiTest.java",
     703, 706, "history"),
    (20778, 0, None, "src/org/sosy_lab/cpachecker/cpa/invariants/CompoundBitVectorInterval.java",
     1585, 1601, "reshaped"),
]

# Functions whose location cannot be selected by the locator because the same
# generated test text exists in many experiment directories.  The entry names
# the one copy that matches the dataset body, its full span and the line that
# carries each comment.
# (function_id, path, first, last, kind, {comment_id: line})
WHOLE_FIXES = [
    (10862,
     "jsoup/a8b6982de98ff76ef254031d7152fff57f6bf941/randoop_6/RegressionTest40.java",
     1455, 1488, "exact", {0: 1474, 1: 1477, 2: 1479}),
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
        elif line.startswith("filename "):
            out["path"] = line[9:]
    return out


def main() -> int:
    path = OUT / "functions_located.jsonl"
    rows = [json.loads(x) for x in path.read_text().splitlines() if x.strip()]
    by_id = {r["id"]: r for r in rows}
    _, tesoro_comments = load_tesoro()
    n = 0
    for fid, cid, rev, fpath, first, last, kind in FIXES:
        loc = by_id.get(fid)
        if not loc:
            continue
        d = repo_dir(loc["repo"])
        # blame the first line that carries text: the opening /** can be older
        lines = subprocess.run(["git", "-C", str(d), "show", f"{rev}:{fpath}"],
                               capture_output=True, text=True).stdout.splitlines() \
            if rev else (d / fpath).read_text(errors="replace").splitlines()
        target = next((i for i in range(first, last + 1) if core(lines[i - 1])), first)
        info = blame(d, rev, fpath, target)
        for c in loc.get("comments") or []:
            if c["comment_id"] != cid:
                continue
            c.update({"lines": list(range(first, last + 1)), "match": kind,
                      "commit": info.get("sha"), "author": info.get("author"),
                      "author_time": info.get("date"), "summary": info.get("summary"),
                      "path_snapshot": fpath, "path_at": info.get("path") or fpath})
            if rev:
                c["at_commit"] = rev
            else:
                c.pop("at_commit", None)
            n += 1
    for fid, fpath, first, last, kind, comment_lines in WHOLE_FIXES:
        loc = by_id.get(fid)
        if not loc:
            continue
        d = repo_dir(loc["repo"])
        if not loc.get("comments"):
            loc["comments"] = [
                {"comment_id": c["comment_id"], "comment": c["comment"],
                 "classification": c.get("classification"), "lines": []}
                for c in tesoro_comments.get(fid, [])
            ]
            loc["n_comments"] = len(loc["comments"])
        loc.update({"path": fpath, "start": first, "end": last,
                    "ratio": 1.0, "line_ratio": 1.0, "tok_ratio": 0.0,
                    "status": "ok", "confidence": "high", "match": kind})
        loc.pop("reason", None)
        loc.pop("rival", None)
        loc.pop("ast_span_shift", None)
        for c in loc.get("comments") or []:
            ln = comment_lines.get(c["comment_id"])
            if not ln:
                continue
            info = blame(d, None, fpath, ln)
            c.update({"lines": [ln], "match": kind, "commit": info.get("sha"),
                      "author": info.get("author"), "author_time": info.get("date"),
                      "summary": info.get("summary"), "path_snapshot": fpath,
                      "path_at": info.get("path") or fpath})
            c.pop("at_commit", None)
            n += 1
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    tmp.replace(path)
    print(f"applied {n} manual mappings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
