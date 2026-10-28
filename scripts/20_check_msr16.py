#!/usr/bin/env python3
"""Check the MSR16 exports against Maldonado's dataset format.

The reference file (`technical_debt_dataset.csv` from
github.com/maldonado/tse.satd.data) has three columns

    projectname,classification,commenttext

and the label vocabulary WITHOUT_CLASSIFICATION / DESIGN / IMPLEMENTATION /
DEFECT / DOCUMENTATION / TEST. This script checks the same shape in our output,
plus the invariants that matter for the versioned file.

    python3 scripts/20_check_msr16.py [--ref path/to/technical_debt_dataset.csv]
"""
from __future__ import annotations

import collections
import csv
import re
import sys

from common import OUT

COLUMNS = ["projectname", "classification", "commenttext"]
LABELS = {"WITHOUT_CLASSIFICATION", "DESIGN", "IMPLEMENTATION", "DEFECT",
          "DOCUMENTATION", "TEST"}
VERSIONED = re.compile(r"^[^\s@]+/[^\s@]+(@[0-9a-f]{7,40})?$")


def load(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def main() -> int:
    problems = []
    plain = load(OUT / "tesoro_msr16.csv")
    vers = load(OUT / "tesoro_msr16_versioned.csv")
    prov = load(OUT / "tesoro_provenance.csv")

    for name, rows in (("tesoro_msr16.csv", plain), ("tesoro_msr16_versioned.csv", vers)):
        cols = list(rows[0]) if rows else []
        if cols != COLUMNS:
            problems.append(f"{name}: columns {cols} != {COLUMNS}")
        labels = {r["classification"] for r in rows}
        if labels - LABELS:
            problems.append(f"{name}: labels outside the MSR16 set: {sorted(labels - LABELS)}")
        if any(not r["classification"] or not r["commenttext"].strip() for r in rows):
            problems.append(f"{name}: empty classification or commenttext")
        if any(r["classification"] == "NONSATD" for r in rows):
            problems.append(f"{name}: NONSATD must be exported as WITHOUT_CLASSIFICATION")
        with open(OUT / name, "rb") as f:
            if f.read(3) == b"\xef\xbb\xbf":
                problems.append(f"{name}: has a UTF-8 BOM")
        print(f"{name}: {len(rows)} rows, labels "
              f"{dict(collections.Counter(r['classification'] for r in rows))}")

    if len(plain) != len(prov) or len(vers) != len(prov):
        problems.append(f"row counts differ: plain {len(plain)}, versioned {len(vers)}, "
                        f"provenance {len(prov)}")
    bad = [r["projectname"] for r in vers if not VERSIONED.match(r["projectname"])]
    if bad:
        problems.append(f"versioned projectname not owner/name[@sha]: {bad[:3]}")
    # a row without a located comment has no commit to pin, so it keeps the name
    unversioned = sum(1 for r in vers if "@" not in r["projectname"])
    print(f"versioned rows without a commit: {unversioned} (no version can be given)")
    multi = sum(1 for r in plain if "\n" in r["commenttext"])
    print(f"multi-line commenttext: {multi} (the reference keeps newlines too)")

    if "--ref" in sys.argv:
        ref = load(sys.argv[sys.argv.index("--ref") + 1])
        ref_labels = {r["classification"] for r in ref}
        print(f"reference: {len(ref)} rows, columns {list(ref[0])}, "
              f"labels {sorted(ref_labels)}")
        if list(ref[0]) != COLUMNS:
            problems.append("reference columns differ (the export follows this format)")
        if LABELS - ref_labels:
            problems.append(f"labels missing from the reference: {sorted(LABELS - ref_labels)}")
        extra = {r["classification"] for r in plain} - ref_labels
        if extra:
            problems.append(f"our labels not in the reference: {sorted(extra)}")

    if problems:
        print("\nFAIL")
        for p in problems:
            print("  -", p)
        return 1
    print("\nOK: columns, label vocabulary and row counts match the MSR16 format")
    return 0


if __name__ == "__main__":
    sys.exit(main())
