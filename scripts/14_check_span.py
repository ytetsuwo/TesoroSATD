"""Inspect every CSV row at its own path_snapshot/snapshot_commit using the AST.

No skips for history, relocated, reshaped, weak_match or unreadable sources.
--json writes a per-comment record to work/span_check.jsonl.
For complete attribution evidence use 27_strict_audit.py.
"""
from collections import Counter
import sys

from common import WORK, write_jsonl
from strict_verification import Auditor, audit_rows, verdict


def main():
    auditor = Auditor()
    records = []
    for row in audit_rows():
        if not row["commit_id"] or not row["comment_line"]:
            check = verdict("unmapped", "no recorded provenance")
        else:
            check = auditor.syntax(row)["ast"]
        records.append({"function_id": row["function_id"], "comment_id": row["comment_id"],
                        "repo": row["repo"], "match": row["match"], "status": check["status"], "ast": check})
    counts = Counter(record["status"] for record in records)
    print(f"AST rows: {len(records)}; {dict(counts)}")
    for record in [r for r in records if r["status"] in ("mismatch", "unverifiable")][:20]:
        print(f"  {record['function_id']}:{record['comment_id']} {record['status']}: {record['ast']['reason']}")
    if "--json" in sys.argv:
        write_jsonl(WORK / "span_check.jsonl", records)
    # Review is a reported uncertainty, not proof of a wrong mapping.
    return int(bool(counts["mismatch"] or counts["unverifiable"]))


if __name__ == "__main__":
    sys.exit(main())
