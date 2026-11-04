#!/usr/bin/env python3
"""Evaluate frozen code/text embeddings with logistic regression on Tesoro.

The split is the same five-group project split used in P0/P1.  A model is only
used as a feature extractor; the classifier is a plain logistic regression.

    python experiments/modernbert_satd/embedding_eval.py \
        --model Qwen/Qwen3-Embedding-0.6B \
        --output experiments/modernbert_satd/results/p2/qwen3_0.6b.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold
from transformers import AutoModel, AutoTokenizer

def read_rows(path: Path):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    texts = [row["comment_text"] for row in rows]
    labels = np.array([1 if row["satd"] == "1" else 0 for row in rows])
    projects = np.array([row.get("project_name", "") for row in rows])
    return rows, texts, labels, projects


def mean_pool(last_hidden, mask):
    mask = mask.unsqueeze(-1).to(last_hidden.dtype)
    return (last_hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-6)


def best_threshold(labels, scores):
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
    values = []
    for fold in np.unique(folds):
        other = folds != fold
        threshold, _ = best_threshold(labels[other], scores[other])
        values.append(f1_score(labels[folds == fold], scores[folds == fold] >= threshold,
                               zero_division=0))
    return round(float(np.mean(values)), 4)


def metrics(labels, scores):
    predicted = (scores >= 0.5).astype(int)
    return {
        "precision": round(float(precision_score(labels, predicted, zero_division=0)), 4),
        "recall": round(float(recall_score(labels, predicted, zero_division=0)), 4),
        "f1": round(float(f1_score(labels, predicted, zero_division=0)), 4),
        "auc": round(float(roc_auc_score(labels, scores)), 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path,
                        default=Path(__file__).resolve().parent / "data/tesoro_views.csv.gz")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=256)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--pooling", choices=["mean", "last"], default="mean")
    parser.add_argument("--trust-remote-code", action="store_true")
    args = parser.parse_args()

    rows, texts, labels, projects = read_rows(args.csv)
    if args.limit:
        keep = np.arange(min(args.limit, len(rows)))
        rows = [rows[i] for i in keep]
        texts = [texts[i] for i in keep]
        labels = labels[keep]
        projects = projects[keep]

    device = ("cuda" if torch.cuda.is_available()
              else "mps" if torch.backends.mps.is_available() else "cpu")
    tokenizer = AutoTokenizer.from_pretrained(
        args.model, trust_remote_code=args.trust_remote_code)
    model = AutoModel.from_pretrained(
        args.model, trust_remote_code=args.trust_remote_code).to(device)
    model.eval()
    print(f"{args.model}; rows {len(texts)}; device {device}", flush=True)

    features = []
    with torch.inference_mode():
        for start in range(0, len(texts), args.batch):
            batch = texts[start:start + args.batch]
            encoded = tokenizer(batch, padding=True, truncation=True,
                                max_length=args.max_length, return_tensors="pt")
            encoded = {key: value.to(device) for key, value in encoded.items()}
            output = model(**encoded)
            hidden = output.last_hidden_state
            if args.pooling == "last":
                lengths = encoded["attention_mask"].sum(1) - 1
                vectors = hidden[torch.arange(hidden.size(0)), lengths]
            else:
                vectors = mean_pool(hidden, encoded["attention_mask"])
            vectors = torch.nn.functional.normalize(vectors, dim=-1)
            features.append(vectors.cpu().numpy().astype(np.float32))
            if (start // args.batch + 1) % 20 == 0:
                print(f"  {start + len(batch)}/{len(texts)}", flush=True)
    features = np.concatenate(features)
    print(f"features {features.shape}", flush=True)

    splits = list(GroupKFold(5).split(features, labels, projects))
    scores = np.zeros(len(labels))
    per_fold = []
    for fold, (train, test) in enumerate(splits, 1):
        classifier = LogisticRegression(max_iter=3000, class_weight="balanced")
        classifier.fit(features[train], labels[train])
        scores[test] = classifier.predict_proba(features[test])[:, 1]
        fold_metrics = metrics(labels[test], scores[test])
        per_fold.append({"fold": fold, **fold_metrics})
        print(f"  fold {fold} F1 {fold_metrics['f1']:.4f}", flush=True)

    result = {
        "model": args.model,
        "pooling": args.pooling,
        "rows": len(labels),
        "satd": int(labels.sum()),
        "split": "project",
        "mean": metrics(labels, scores),
        "per_fold": per_fold,
    }
    fold_vector = np.zeros(len(labels), dtype=int)
    for fold, (_, test) in enumerate(splits, 1):
        fold_vector[test] = fold
    result["f1_honest"] = honest_f1(labels, fold_vector, scores)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["mean"]), flush=True)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
