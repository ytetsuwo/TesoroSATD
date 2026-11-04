#!/usr/bin/env python3
"""Summarize the frozen-embedding + logistic-regression runs."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P2 = ROOT / "experiments/modernbert_satd/results/p2"


def main() -> int:
    rows = []
    for path in sorted(P2.glob("*.json")):
        result = json.loads(path.read_text())
        rows.append({
            "file": path.name,
            "model": result["model"],
            "pooling": result.get("pooling", "mean"),
            "f1": result["mean"]["f1"],
            "precision": result["mean"]["precision"],
            "recall": result["mean"]["recall"],
            "auc": result["mean"]["auc"],
            "f1_honest": result["f1_honest"],
        })
    rows.sort(key=lambda row: -row["f1"])
    for row in rows:
        label = f"{row['model']} [{row['pooling']}]"
        print(f"{label:60s} F1 {row['f1']:.4f} "
              f"honest {row['f1_honest']:.4f} P {row['precision']:.4f} "
              f"R {row['recall']:.4f} AUC {row['auc']:.4f}")
    output = P2.parent / "p2_summary.json"
    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
