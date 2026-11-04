#!/usr/bin/env python3
"""Combine the ModernBERT text/log/code out-of-fold predictions.

All three models are trained on the same Maldonado rows and the same 5-fold
split, so their ``row`` columns line up.  The script compares:

* each input on its own,
* probability averaging,
* logit averaging.

    python experiments/modernbert_satd/ensemble.py \
        --text results/maldonado_predictions.csv \
        --log results/maldonado_log_predictions.csv \
        --code results/maldonado_code_predictions.csv \
        --output results/maldonado_ensemble.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

import numpy as np
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score


def read_predictions(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    scores = np.empty(len(rows))
    labels = np.empty(len(rows), dtype=int)
    folds = np.empty(len(rows), dtype=int)
    for row in rows:
        index = int(row["row"])
        scores[index] = float(row["score"])
        labels[index] = int(row["label"])
        folds[index] = int(row["fold"])
    return scores, labels, folds


def binary_metrics(labels, scores, threshold=0.5):
    predicted = (scores >= threshold).astype(int)
    return {
        "precision": round(float(precision_score(labels, predicted, zero_division=0)), 4),
        "recall": round(float(recall_score(labels, predicted, zero_division=0)), 4),
        "f1": round(float(f1_score(labels, predicted, zero_division=0)), 4),
        "auc": round(float(roc_auc_score(labels, scores)), 4),
    }


def logit(probability):
    probability = np.clip(probability, 1e-6, 1 - 1e-6)
    return np.log(probability / (1 - probability))


def sigmoid(value):
    return 1.0 / (1.0 + np.exp(-value))


def best_threshold(labels, scores):
    """Threshold with the best F1 over the distinct observed scores."""
    order = np.argsort(-scores, kind="stable")
    ordered_scores = scores[order]
    ordered_labels = labels[order]
    tp = np.cumsum(ordered_labels)
    fp = np.cumsum(1 - ordered_labels)
    positives = int(ordered_labels.sum())
    last = np.r_[np.diff(ordered_scores) != 0, True]
    tp, fp = tp[last], fp[last]
    thresholds = ordered_scores[last]
    f1 = 2 * tp / (2 * tp + fp + (positives - tp))
    index = int(np.argmax(f1))
    return float(thresholds[index]), float(f1[index])


def honest_f1(labels, folds, scores):
    """Pick the threshold on the other folds and apply it to each held-out fold."""
    values = []
    for fold in np.unique(folds):
        other = folds != fold
        threshold, _ = best_threshold(labels[other], scores[other])
        values.append(f1_score(labels[folds == fold], scores[folds == fold] >= threshold,
                               zero_division=0))
    return round(float(np.mean(values)), 4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", type=Path, required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--code", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()

    arrays = {}
    labels = folds = None
    for name, path in (("text", args.text), ("log", args.log), ("code", args.code)):
        scores, current_labels, current_folds = read_predictions(path)
        if labels is None:
            labels, folds = current_labels, current_folds
        elif not np.array_equal(labels, current_labels):
            raise SystemExit(f"{name}: labels do not align with text predictions")
        arrays[name] = scores

    print(f"rows {len(labels)}; SATD {int(labels.sum())}")
    payload = {
        "rows": len(labels),
        "satd": int(labels.sum()),
        "threshold": args.threshold,
        "single": {},
        "ensembles": {},
        "correlation": {},
    }
    for name, scores in arrays.items():
        payload["correlation"][name] = round(float(np.corrcoef(arrays["text"], scores)[0, 1]), 4)
    print("\nsingle models")
    for name, scores in arrays.items():
        result = binary_metrics(labels, scores, args.threshold)
        result["f1_honest"] = honest_f1(labels, folds, scores)
        payload["single"][name] = result
        print(f"  {name:5s} P {result['precision']:.4f} R {result['recall']:.4f} "
              f"F1 {result['f1']:.4f} AUC {result['auc']:.4f} "
              f"honest {result['f1_honest']:.4f}")

    combinations = [
        ("text+log", ("text", "log")),
        ("text+code", ("text", "code")),
        ("log+code", ("log", "code")),
        ("text+log+code", ("text", "log", "code")),
    ]
    print("\nensembles")
    for name, members in combinations:
        parts = [arrays[member] for member in members]
        mean_probability = np.mean(parts, axis=0)
        mean_logit = sigmoid(np.mean([logit(part) for part in parts], axis=0))
        row = {}
        for mode, scores in (("probability", mean_probability), ("logit", mean_logit)):
            result = binary_metrics(labels, scores, args.threshold)
            result["f1_honest"] = honest_f1(labels, folds, scores)
            row[mode] = result
            print(f"  {name:13s} {mode:11s} P {result['precision']:.4f} "
                  f"R {result['recall']:.4f} F1 {result['f1']:.4f} "
                  f"AUC {result['auc']:.4f} honest {result['f1_honest']:.4f}")
        payload["ensembles"][name] = row

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nwrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
