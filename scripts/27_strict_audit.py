#!/usr/bin/env python3
"""Audit every exported comment without silently excluding historical copies.

Default: produce evidence, including review/mismatch results, without deleting
provenance rows. --fail-on mismatch,unverifiable is an optional quality gate.
"""
import argparse
from collections import Counter
import hashlib
import json
import sys

from common import OUT, write_jsonl
from strict_verification import Auditor, STATUSES, audit_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--syntax-only", action="store_true", help="inspect AST/body without Git attribution")
    parser.add_argument("--fail-on", default="", help="comma-separated statuses that cause exit 1")
    args = parser.parse_args()
    rejected = set(filter(None, args.fail_on.split(",")))
    if rejected - set(STATUSES):
        parser.error("unknown failure status")
    auditor = Auditor()
    rows = audit_rows()
    records = []
    for i, row in enumerate(rows, 1):
        records.append(auditor.verify(row, full=not args.syntax_only))
        if i % 500 == 0:
            print(f"audited {i}/{len(rows)}", flush=True)
    counts = Counter(record["status"] for record in records)
    by_check = {}
    by_match = {}
    for record in records:
        by_match.setdefault(record["match"] or "unmapped", Counter())[record["status"]] += 1
        for key, check in record["checks"].items():
            by_check.setdefault(key, Counter())[check["status"]] += 1
    report = {"schema_version": 1, "rows": len(rows),
              "mapped": sum(bool(row["commit_id"] and row["comment_line"]) for row in rows),
              "provenance_sha256": hashlib.sha256((OUT / "tesoro_provenance.csv").read_bytes()).hexdigest(),
              "full_attribution": not args.syntax_only, "counts": dict(counts),
              "by_check": by_check, "by_match": by_match,
              "snapshots_inspected": sum(row["at_head"] != "True" and bool(row["commit_id"]) for row in rows),
              "definition": "automated evidence for the recorded body/location and last writer; not first introduction or unique origin"}
    write_jsonl(OUT / "strict_audit.jsonl", records)
    (OUT / "strict_audit_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    labels = {"confirmed": "確定", "review": "要確認", "mismatch": "不一致",
              "unverifiable": "検証不能", "unmapped": "未対応"}
    summary = ["# 来歴の厳密監査", "", f"対象: {len(rows)} コメント（来歴付き {report['mapped']}）", "",
               "| 判定 | 件数 |", "| --- | ---: |"]
    summary += [f"| {labels[s]} | {counts[s]} |" for s in STATUSES]
    summary += ["", "確定は、記録された本文・関数所属・本文行の最終更新者についての自動検査の判定です。",
                "最初の導入コミットや、同型コピー間の唯一の起源を保証するものではありません。",
                "要確認・不一致・検証不能を来歴付き件数に埋め込まず、別集計します。",
                "", "詳細: `out/strict_audit.jsonl`。本文、AST 所属、blame の元行、コミット中の元位置を記録。"]
    (OUT / "strict_audit_summary.md").write_text("\n".join(summary) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(any(counts[status] for status in rejected))


if __name__ == "__main__":
    sys.exit(main())
