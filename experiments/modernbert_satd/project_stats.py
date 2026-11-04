#!/usr/bin/env python3
"""Project-level statistics for the Tesoro project-split predictions."""
from __future__ import annotations

import csv
import gzip
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.stats import wilcoxon
from sklearn.metrics import f1_score, precision_score, recall_score

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "experiments/modernbert_satd/results"
VIEWS = ROOT / "experiments/modernbert_satd/data/tesoro_views.csv.gz"
LARGE = RESULTS / "p1/modernbert-large_project_seed0.csv"
CODEBERT = RESULTS / "p0/codebert_project_seed0.csv"
MIN_SATD = 5
MIN_ROWS = 10
BOOTSTRAP = 1000


def read_predictions(path):
    scores = {}
    labels = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            index = int(row["row"])
            scores[index] = float(row["score"])
            labels[index] = int(row["label"])
    return scores, labels


def project_metrics(y, p):
    return {
        "precision": round(float(precision_score(y, p, zero_division=0)), 4),
        "recall": round(float(recall_score(y, p, zero_division=0)), 4),
        "f1": round(float(f1_score(y, p, zero_division=0)), 4),
    }


def bootstrap_f1(y, p, rng):
    values = []
    n = len(y)
    for _ in range(BOOTSTRAP):
        index = rng.integers(0, n, n)
        if y[index].sum() == 0:
            continue
        values.append(f1_score(y[index], p[index], zero_division=0))
    if not values:
        return None, None
    return round(float(np.percentile(values, 2.5)), 4), round(float(np.percentile(values, 97.5)), 4)


def main() -> int:
    with gzip.open(VIEWS, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    large, labels = read_predictions(LARGE)
    codebert, _ = read_predictions(CODEBERT)
    y = np.array([labels[i] for i in range(len(rows))])
    large_pred = np.array([large[i] >= 0.5 for i in range(len(rows))], dtype=int)
    codebert_pred = np.array([codebert[i] >= 0.5 for i in range(len(rows))], dtype=int)
    projects = np.array([row.get("project_name", "") for row in rows])

    rng = np.random.default_rng(0)
    by_project = defaultdict(list)
    for index, project in enumerate(projects):
        by_project[project].append(index)
    entries = []
    for project, index in by_project.items():
        index = np.array(index)
        if len(index) < MIN_ROWS or y[index].sum() < MIN_SATD:
            continue
        yy = y[index]
        pp = large_pred[index]
        cc = codebert_pred[index]
        entry = {
            "project": project,
            "rows": len(index),
            "satd": int(yy.sum()),
            "large": project_metrics(yy, pp),
            "codebert": project_metrics(yy, cc),
            "f1_delta": round(float(f1_score(yy, pp, zero_division=0)
                                   - f1_score(yy, cc, zero_division=0)), 4),
        }
        low, high = bootstrap_f1(yy, pp, rng)
        entry["f1_ci95"] = [low, high]
        entries.append(entry)

    entries.sort(key=lambda item: (item["large"]["f1"], -item["rows"], item["project"]))
    deltas = np.array([entry["f1_delta"] for entry in entries])
    f1_values = np.array([entry["large"]["f1"] for entry in entries])
    statistic, pvalue = wilcoxon(deltas) if len(deltas) >= 10 else (None, None)
    summary = {
        "min_satd": MIN_SATD,
        "min_rows": MIN_ROWS,
        "projects": len(entries),
        "large_f1_median": round(float(np.median(f1_values)), 4),
        "large_f1_iqr": [round(float(np.percentile(f1_values, 25)), 4),
                         round(float(np.percentile(f1_values, 75)), 4)],
        "large_f1_below_0.8": int((f1_values < 0.8).sum()),
        "delta_mean": round(float(deltas.mean()), 4),
        "delta_median": round(float(np.median(deltas)), 4),
        "large_better": int((deltas > 0).sum()),
        "codebert_better": int((deltas < 0).sum()),
        "wilcoxon_stat": round(float(statistic), 4) if statistic is not None else None,
        "wilcoxon_p": round(float(pvalue), 4) if pvalue is not None else None,
        "projects_detail": entries,
    }

    print(f"projects with >= {MIN_SATD} SATD and >= {MIN_ROWS} rows: {len(entries)}")
    print(f"ModernBERT-large project F1 median {summary['large_f1_median']:.4f} "
          f"IQR {summary['large_f1_iqr']}")
    print(f"F1 < 0.8: {summary['large_f1_below_0.8']}")
    print(f"delta mean {summary['delta_mean']:+.4f}, median {summary['delta_median']:+.4f}; "
          f"large better {summary['large_better']}, CodeBERT better {summary['codebert_better']}")
    print(f"Wilcoxon p={summary['wilcoxon_p']}")
    print("\nlowest F1")
    for entry in entries[:10]:
        print(f"  {entry['project']:55s} F1 {entry['large']['f1']:.4f} "
              f"CI {entry['f1_ci95']} n={entry['rows']} satd={entry['satd']}")

    output = RESULTS / "p3_project_stats.json"
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
