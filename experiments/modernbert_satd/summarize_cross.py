#!/usr/bin/env python3
"""Summarize the zero-shot Maldonado <-> Tesoro transfer runs."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
P3 = ROOT / "experiments/modernbert_satd/results/p3"


def main() -> int:
    rows = []
    for path in sorted(P3.glob("cross_*.json")):
        if path.name == "cross_summary.json":
            continue
        result = json.loads(path.read_text())
        rows.append({
            "file": path.name,
            "model": result["model"],
            "train": result["train"],
            "eval": result["eval"],
            "train_rows": result["train_rows"],
            "eval_rows": result["eval_rows"],
            "at_0.5": result["at_0.5"],
            "best_target_threshold": result["best_target_threshold"],
            "at_best_target_threshold": result["at_best_target_threshold"],
        })
    rows.sort(key=lambda row: (row["train"], row["model"]))
    for row in rows:
        print(f"{row['model']:55s} {row['train']:10s} -> {row['eval']:10s} "
              f"F1@0.5 {row['at_0.5']['f1']:.4f} "
              f"best-thr F1 {row['at_best_target_threshold']['f1']:.4f} "
              f"R {row['at_0.5']['recall']:.4f}")
    output = P3 / "cross_summary.json"
    output.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
