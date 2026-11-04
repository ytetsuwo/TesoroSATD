#!/usr/bin/env python3
"""Summarize the P1 encoder comparison under the Tesoro project split."""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "experiments/modernbert_satd/results"
P0 = RESULTS / "p0"
P1 = RESULTS / "p1"

MODELS = [
    ("CodeBERT", P0, "codebert"),
    ("ModernBERT-base", P0, "modernbert"),
    ("GraphCodeBERT", P1, "graphcodebert"),
    ("UnixCoder", P1, "unixcoder"),
    ("ModernBERT-large", P1, "modernbert-large"),
]
SEEDS = (0, 1, 2)


def mean_std(values):
    n = len(values)
    mean = sum(values) / n
    variance = sum((value - mean) ** 2 for value in values) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(variance)
    half = 4.303 * std / math.sqrt(n) if n > 1 else 0.0
    return round(mean, 4), round(std, 4), round(half, 4)


def main() -> int:
    summary = {"split": "project", "models": {}}
    codebert_f1 = None
    for label, directory, key in MODELS:
        values = {}
        for seed in SEEDS:
            path = directory / f"{key}_project_seed{seed}.json"
            if path.exists():
                values[seed] = json.loads(path.read_text())["mean"]
        if not values:
            print(f"skip {label}: no runs")
            continue
        model = {}
        for metric in ("precision", "recall", "f1", "auc"):
            numbers = [value[metric] for value in values.values()]
            mean, std, half = mean_std(numbers)
            model[metric] = {"mean": mean, "std": std, "ci95": half,
                             "values": numbers}
        summary["models"][label] = model
        if label == "CodeBERT":
            codebert_f1 = model["f1"]["mean"]
        f1 = model["f1"]
        print(f"{label:18s} F1 {f1['mean']:.4f} ± {f1['ci95']:.4f} "
              f"(values {f1['values']})")

    if codebert_f1 is not None:
        for label, model in summary["models"].items():
            if label == "CodeBERT":
                continue
            delta = round(model["f1"]["mean"] - codebert_f1, 4)
            model["delta_vs_codebert"] = delta
            print(f"  {label:16s} vs CodeBERT {delta:+.4f}")

    output = RESULTS / "p1_summary.json"
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
