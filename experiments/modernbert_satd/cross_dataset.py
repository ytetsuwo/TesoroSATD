#!/usr/bin/env python3
"""Zero-shot cross-dataset SATD evaluation (Maldonado <-> Tesoro).

Train on one dataset and evaluate on the other without target labels.  The
classifier uses a single logit and BCEWithLogits with the training pos_weight,
matching the within-dataset experiments.

    python experiments/modernbert_satd/cross_dataset.py \
        --train maldonado --eval tesoro --model microsoft/codebert-base \
        --output experiments/modernbert_satd/results/p3/cross_maldonado_to_tesoro_codebert.json
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from torch import nn
from transformers import (AutoModelForSequenceClassification, AutoTokenizer,
                          Trainer, TrainingArguments)

ROOT = Path(__file__).resolve().parents[2]
MALDONADO = ROOT.parent / "MaldonadoSATD/research/data/finetune_input.csv.gz"
TESORO = ROOT / "experiments/modernbert_satd/data/tesoro_views.csv.gz"


def read_dataset(name: str, path: Path | None = None):
    path = path or (MALDONADO if name == "maldonado" else TESORO)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    texts = [row["comment_text"] for row in rows]
    labels = np.array([1 if row["satd"] == "1" else 0 for row in rows])
    return rows, texts, labels


class Encoded(torch.utils.data.Dataset):
    def __init__(self, encodings, labels):
        self.encodings = encodings
        self.labels = labels

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, index):
        item = {key: value[index] for key, value in self.encodings.items()}
        item["labels"] = torch.tensor(float(self.labels[index]))
        return item


class BCETrainer(Trainer):
    def __init__(self, *args, pos_weight: float = 1.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.pos_weight = torch.tensor(pos_weight)

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels").float()
        outputs = model(**inputs)
        logits = outputs.logits.reshape(-1)
        weight = self.pos_weight.to(logits.device)
        loss = nn.functional.binary_cross_entropy_with_logits(logits, labels, pos_weight=weight)
        return (loss, outputs) if return_outputs else loss


def metrics(labels, scores, threshold=0.5):
    predicted = (scores >= threshold).astype(int)
    tp = int(((predicted == 1) & (labels == 1)).sum())
    fp = int(((predicted == 1) & (labels == 0)).sum())
    fn = int(((predicted == 0) & (labels == 1)).sum())
    tn = int(((predicted == 0) & (labels == 0)).sum())
    return {
        "threshold": round(float(threshold), 4),
        "precision": round(float(precision_score(labels, predicted, zero_division=0)), 4),
        "recall": round(float(recall_score(labels, predicted, zero_division=0)), 4),
        "f1": round(float(f1_score(labels, predicted, zero_division=0)), 4),
        "auc": round(float(roc_auc_score(labels, scores)), 4),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
    }


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
    return float(thresholds[index])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", choices=["maldonado", "tesoro"], required=True)
    parser.add_argument("--eval", choices=["maldonado", "tesoro"], required=True)
    parser.add_argument("--train-csv", type=Path, default=None)
    parser.add_argument("--eval-csv", type=Path, default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--batch", type=int, default=128)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    train_rows, train_texts, train_labels = read_dataset(args.train, args.train_csv)
    eval_rows, eval_texts, eval_labels = read_dataset(args.eval, args.eval_csv)
    if args.limit:
        train_texts = train_texts[:args.limit]
        train_labels = train_labels[:args.limit]

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    train_enc = tokenizer(train_texts, truncation=True, padding="max_length",
                          max_length=args.max_length, return_tensors="pt")
    eval_enc = tokenizer(eval_texts, truncation=True, padding="max_length",
                         max_length=args.max_length, return_tensors="pt")
    train_set = Encoded(train_enc, train_labels)
    eval_set = Encoded(eval_enc, eval_labels)

    model = AutoModelForSequenceClassification.from_pretrained(args.model, num_labels=1)
    positives = int(train_labels.sum())
    negatives = len(train_labels) - positives
    trainer = BCETrainer(
        model=model,
        train_dataset=train_set,
        args=TrainingArguments(
            output_dir=str(args.output.parent / "trainer_tmp"),
            num_train_epochs=args.epochs,
            per_device_train_batch_size=args.batch,
            learning_rate=args.lr,
            logging_steps=50,
            save_strategy="no",
            report_to=[],
            remove_unused_columns=False,
        ),
        pos_weight=negatives / max(positives, 1),
    )
    trainer.train()
    predictions = trainer.predict(eval_set).predictions.reshape(-1)
    scores = 1.0 / (1.0 + np.exp(-predictions))

    result = {
        "model": args.model,
        "train": args.train,
        "eval": args.eval,
        "train_rows": len(train_labels),
        "train_satd": int(train_labels.sum()),
        "eval_rows": len(eval_labels),
        "eval_satd": int(eval_labels.sum()),
        "at_0.5": metrics(eval_labels, scores, 0.5),
        "best_target_threshold": round(best_threshold(eval_labels, scores), 4),
    }
    threshold = best_threshold(eval_labels, scores)
    result["at_best_target_threshold"] = metrics(eval_labels, scores, threshold)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["at_0.5"], indent=2))
    print(f"best target threshold {threshold:.4f}")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
