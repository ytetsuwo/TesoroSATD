#!/usr/bin/env python3
"""Verify comment provenance.

  python3 scripts/05_verify.py [N]     fixed random sample, 3 checks
  python3 scripts/05_verify.py --full  every mapped row, text check only

Rules live in provenance_checks; this file selects rows and reports results.
"""
import csv
import random
import sys

from common import OUT
from comment_matching import MARKER_ANY as MARKER
from provenance_checks import CHECKS, check_blame, check_commit, check_text, content, ctext
from source_files import SourceFiles


def rows():
    with open(OUT / "tesoro_provenance.csv") as file:
        return [row for row in csv.DictReader(file) if row["commit_id"] and row["comment_line"]]


def main():
    rs = rows()
    sources = SourceFiles()
    checks = (("text", lambda row: check_text(row, sources)),
              ("blame", check_blame),
              ("show", lambda row: check_commit(row, sources)))
    if "--full" in sys.argv:
        bad = []
        for row in rs:
            error = check_text(row, sources)
            if error:
                bad.append((row, error))
        print(f"text: {len(rs) - len(bad)} ok / {len(bad)} bad of {len(rs)}")
        for row, error in bad[:30]:
            print(f"  {row['function_id']:>6} {row['repo']} {row['path']}:{row['comment_line']} "
                  f"[{row['match']}] {error}")
        return

    n = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 12
    sample = random.Random(7).sample(rs, n)
    print(f"{'id':>6}  text  blame  show   repo path:line")
    tally = {key: 0 for key, _ in checks}
    for row in sample:
        results = {key: check(row) for key, check in checks}
        for key, error in results.items():
            if error is None:
                tally[key] += 1
        flags = "".join("  ok  " if results[key] is None else " BAD  " for key, _ in checks)
        print(f"{row['function_id']:>6} {flags} {row['repo']} {row['path']}:{row['comment_line']} "
              f"[{row['match']} {row['function_ratio']}] {row['commit_short']} {row['commit_date'][:10]}")
        for key, error in results.items():
            if error:
                print(f"       {key}: {error}")
    print("\n" + ", ".join(f"{key} {count}/{len(sample)}" for key, count in tally.items()))


if __name__ == "__main__":
    main()
