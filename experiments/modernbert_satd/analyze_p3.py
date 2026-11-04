#!/usr/bin/env python3
"""Error analysis for the Tesoro project-split models.

The CSV predictions are row-aligned with ``tesoro_views.csv.gz``.  This script
reports the confusion matrix, SATD-type recall, project-level errors, length
bins and the paired comparison between ModernBERT-large and CodeBERT.

    python experiments/modernbert_satd/analyze_p3.py
"""
from __future__ import annotations

import csv
import gzip
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "experiments/modernbert_satd/results"
VIEWS = ROOT / "experiments/modernbert_satd/data/tesoro_views.csv.gz"
LARGE = RESULTS / "p1/modernbert-large_project_seed0.csv"
CODEBERT = RESULTS / "p0/codebert_project_seed0.csv"


def read_views():
    with gzip.open(VIEWS, "rt", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def read_predictions(path):
    scores = {}
    labels = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            index = int(row["row"])
            scores[index] = float(row["score"])
            labels[index] = int(row["label"])
    return scores, labels


def metrics(labels, predicted):
    return {
        "precision": round(float(precision_score(labels, predicted, zero_division=0)), 4),
        "recall": round(float(recall_score(labels, predicted, zero_division=0)), 4),
        "f1": round(float(f1_score(labels, predicted, zero_division=0)), 4),
    }


def main() -> int:
    rows = read_views()
    large, labels = read_predictions(LARGE)
    codebert, _ = read_predictions(CODEBERT)
    y = np.array([labels[i] for i in range(len(rows))])
    large_scores = np.array([large[i] for i in range(len(rows))])
    codebert_scores = np.array([codebert[i] for i in range(len(rows))])
    large_pred = (large_scores >= 0.5).astype(int)
    codebert_pred = (codebert_scores >= 0.5).astype(int)

    tp = int(np.sum((large_pred == 1) & (y == 1)))
    fp = int(np.sum((large_pred == 1) & (y == 0)))
    fn = int(np.sum((large_pred == 0) & (y == 1)))
    tn = int(np.sum((large_pred == 0) & (y == 0)))
    summary = {
        "model": "ModernBERT-large",
        "seed": 0,
        "split": "project",
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "metrics": metrics(y, large_pred),
        "classes": {},
        "projects": [],
        "length_bins": [],
        "comparison": {},
        "errors": {"false_positives": [], "false_negatives": []},
    }

    classifications = [row.get("classification", "") for row in rows]
    for label in sorted(set(classifications)):
        index = np.array([value == label for value in classifications])
        count = int(index.sum())
        positives = int(y[index].sum())
        predicted_positive = int(large_pred[index].sum())
        entry = {
            "count": count,
            "satd": positives,
            "predicted_positive": predicted_positive,
        }
        if label == "NONSATD":
            entry["specificity"] = round(1 - predicted_positive / count, 4)
        else:
            entry["recall"] = round(predicted_positive / positives, 4) if positives else 0.0
        summary["classes"][label] = entry

    projects = [row.get("project_name", "") for row in rows]
    by_project = defaultdict(list)
    for index, project in enumerate(projects):
        by_project[project].append(index)
    project_rows = []
    for project, index in by_project.items():
        index = np.array(index)
        if len(index) < 10 or int(y[index].sum()) < 5:
            continue
        yy = y[index]
        pp = large_pred[index]
        tp_p = int(((pp == 1) & (yy == 1)).sum())
        fp_p = int(((pp == 1) & (yy == 0)).sum())
        fn_p = int(((pp == 0) & (yy == 1)).sum())
        tn_p = int(((pp == 0) & (yy == 0)).sum())
        project_rows.append({
            "project": project,
            "rows": len(index),
            "satd": int(yy.sum()),
            "tp": tp_p, "fp": fp_p, "fn": fn_p, "tn": tn_p,
            "f1": round(float(f1_score(yy, pp, zero_division=0)), 4),
            "recall": round(float(recall_score(yy, pp, zero_division=0)), 4),
        })
    project_rows.sort(key=lambda row: (row["f1"], -row["rows"], row["project"]))
    summary["projects"] = project_rows[:20]

    words = np.array([len(row.get("comment_text", "").split()) for row in rows])
    boundaries = [0, 5, 10, 20, 40, 80, 10 ** 9]
    for low, high in zip(boundaries, boundaries[1:]):
        index = (words >= low) & (words < high)
        if not index.any():
            continue
        label = f"{low}-{high - 1}" if high < 10 ** 9 else f"{low}+"
        yy = y[index]
        pp = large_pred[index]
        summary["length_bins"].append({
            "bin": label,
            "rows": int(index.sum()),
            "satd": int(yy.sum()),
            **metrics(yy, pp),
        })

    correct_large = large_pred == y
    correct_codebert = codebert_pred == y
    summary["comparison"] = {
        "large_correct_codebert_wrong": int((correct_large & ~correct_codebert).sum()),
        "codebert_correct_large_wrong": int((~correct_large & correct_codebert).sum()),
        "both_correct": int((correct_large & correct_codebert).sum()),
        "both_wrong": int((~correct_large & ~correct_codebert).sum()),
    }

    def example(index):
        return {
            "project": projects[index],
            "classification": classifications[index],
            "comment": rows[index].get("comment_text", "")[:300],
            "score": round(float(large_scores[index]), 4),
            "label": int(y[index]),
        }

    for index in np.argsort(-large_scores):
        if large_pred[index] == 1 and y[index] == 0:
            summary["errors"]["false_positives"].append(example(int(index)))
            if len(summary["errors"]["false_positives"]) >= 20:
                break
    for index in np.argsort(large_scores):
        if large_pred[index] == 0 and y[index] == 1:
            summary["errors"]["false_negatives"].append(example(int(index)))
            if len(summary["errors"]["false_negatives"]) >= 20:
                break

    print(json.dumps(summary["confusion"], indent=2))
    print(json.dumps(summary["metrics"], indent=2))
    print("\nclasses")
    for label, entry in summary["classes"].items():
        print(f"  {label:16s} {entry}")
    print("\nprojects with >=10 rows (lowest F1)")
    for entry in summary["projects"][:10]:
        print(f"  {entry['project']:55s} F1 {entry['f1']:.4f} "
              f"n={entry['rows']} satd={entry['satd']}")
    print("\nlength bins")
    for entry in summary["length_bins"]:
        print(f"  {entry['bin']:8s} n={entry['rows']:4d} F1 {entry['f1']:.4f} "
              f"R {entry['recall']:.4f}")
    print("\ncomparison", summary["comparison"])

    output = RESULTS / "p3_error_analysis.json"
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nwrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
