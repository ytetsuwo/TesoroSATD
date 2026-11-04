#!/usr/bin/env python3
"""Summarize the P0 project/duplicate-held-out runs.

    python experiments/modernbert_satd/summarize_p0.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "experiments/modernbert_satd/results"
P0 = RESULTS / "p0"
MODELS = {
    "CodeBERT": ("codebert", "tesoro_codebert_text.json"),
    "ModernBERT": ("modernbert", "tesoro_modernbert.json"),
}
SPLITS = ("project", "duplicate-free")
SEEDS = (0, 1, 2)


def mean_std(values):
    n = len(values)
    mean = sum(values) / n
    variance = sum((value - mean) ** 2 for value in values) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(variance)
    # t(0.975, df=2) = 4.303; enough for the three-seed runs.
    half = 4.303 * std / math.sqrt(n) if n > 1 else 0.0
    return round(mean, 4), round(std, 4), round(half, 4)


def main() -> int:
    summary = {"splits": {}, "baseline": {}}
    for label, (key, baseline_name) in MODELS.items():
        baseline = json.loads((RESULTS / baseline_name).read_text())
        summary["baseline"][label] = baseline["mean"]
        print(f"\n{label}: random baseline {baseline['mean']}")
        for split in SPLITS:
            values = {}
            for seed in SEEDS:
                path = P0 / f"{key}_{split}_seed{seed}.json"
                if not path.exists():
                    continue
                values[seed] = json.loads(path.read_text())["mean"]
            if not values:
                continue
            result = {}
            for metric in ("precision", "recall", "f1", "auc"):
                numbers = [value[metric] for value in values.values()]
                mean, std, half = mean_std(numbers)
                result[metric] = {"mean": mean, "std": std, "ci95": half,
                                  "values": numbers}
            summary["splits"].setdefault(split, {})[label] = result
            f1 = result["f1"]
            print(f"  {split:14s} F1 {f1['mean']:.4f} ± {f1['ci95']:.4f} "
                  f"(values {f1['values']})")
    for split, models in summary["splits"].items():
        if "CodeBERT" not in models or "ModernBERT" not in models:
            continue
        deltas = [a - b for a, b in zip(models["CodeBERT"]["f1"]["values"],
                                        models["ModernBERT"]["f1"]["values"])]
        mean, std, half = mean_std(deltas)
        models["delta_codebert_minus_modernbert"] = {
            "mean": mean, "std": std, "ci95": half, "values": [round(x, 4) for x in deltas]
        }
        print(f"  {split:14s} CodeBERT - ModernBERT F1 "
              f"{mean:+.4f} ± {half:.4f}")
    output = RESULTS / "p0_summary.json"
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
