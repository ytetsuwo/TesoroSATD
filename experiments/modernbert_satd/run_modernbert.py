#!/usr/bin/env python3
"""Fine-tune ModernBERT for binary SATD classification.

The setup follows ``MaldonadoSATD/research/finetune_bert.py``: stratified
5-fold splits, class weighting with ``pos_weight``, and the same metrics.
Two inputs are supported:

* ``maldonado`` -- the 61,628 comments with a resolved introduction commit
  (``satd`` is 0/1).
* ``tesoro`` -- the 4,981 Tesoro comments; SATD is every class except
  ``NONSATD``.

Examples (Apple Silicon uses MPS automatically)::

    /tmp/mlenv-modernbert/bin/python \
        experiments/modernbert_satd/run_modernbert.py \
        --dataset tesoro --folds 5 --epochs 3 \
        --output experiments/modernbert_satd/results/tesoro_modernbert.json

    /tmp/mlenv-modernbert/bin/python \
        experiments/modernbert_satd/run_modernbert.py \
        --dataset maldonado --fold 1 --epochs 1 \
        --output experiments/modernbert_satd/results/maldonado_fold1.json

Use ``--limit`` only for a smoke test; it subsamples the training rows of the
selected fold, never the test rows.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import os
import random
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold, LeaveOneGroupOut, StratifiedKFold
from torch import nn
from torch.utils.data import Dataset
from transformers import (
    AutoModelForSequenceClassification,
    AutoTokenizer,
    Trainer,
    TrainingArguments,
)

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

if torch.cuda.is_available():
    # TF32 is accurate enough for this classification task and much faster
    # than full FP32 matmul on Ampere/Ada GPUs.
    torch.set_float32_matmul_precision("high")

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MALDONADO = ROOT.parent / "MaldonadoSATD" / "research/data/finetune_input.csv.gz"
DEFAULT_MALDONADO_CODE = (
    ROOT.parent / "MaldonadoSATD" / "research/data/finetune_input_code.csv.gz"
)
DEFAULT_TESORO = ROOT / "tesoro/data/tesoro/tesoro_comment.json"
DEFAULT_MODEL = "answerdotai/ModernBERT-base"


def read_maldonado(path: Path, input_kind: str):
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    column = {"text": "comment_text", "log": "commit_log", "code": "code"}[input_kind]
    texts = [row[column] for row in rows]
    labels = np.array([1 if row.get("satd") == "1" else 0 for row in rows])
    projects = np.array([row.get("project_name", "") for row in rows])
    return rows, texts, labels, projects


def read_tesoro(path: Path, input_kind: str):
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]
    if input_kind == "code":
        # The full function is the code view; fall back to the 2-line context.
        texts = [row.get("code") or row.get("code_context_2", "") for row in rows]
    else:
        texts = [row["comment"] for row in rows]
    labels = np.array([0 if row["classification"] == "NONSATD" else 1 for row in rows])
    projects = np.array([""] * len(rows))
    return rows, texts, labels, projects


class Comments(Dataset):
    def __init__(self, encodings, labels):
        self.encodings = encodings
        self.labels = labels

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, index):
        item = {key: value[index] for key, value in self.encodings.items()}
        item["labels"] = torch.tensor(self.labels[index], dtype=torch.float)
        return item


class WeightedTrainer(Trainer):
    def __init__(self, pos_weight: float, **kwargs):
        super().__init__(**kwargs)
        self.pos_weight = pos_weight

    def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
        labels = inputs.pop("labels")
        outputs = model(**inputs)
        logits = outputs.logits.view(-1)
        loss = nn.functional.binary_cross_entropy_with_logits(
            logits, labels.float(),
            pos_weight=torch.tensor(self.pos_weight, device=logits.device),
        )
        return (loss, outputs) if return_outputs else loss


def metrics(labels, scores):
    predicted = (scores >= 0.5).astype(int)
    result = {
        "precision": round(precision_score(labels, predicted, zero_division=0), 4),
        "recall": round(recall_score(labels, predicted, zero_division=0), 4),
        "f1": round(f1_score(labels, predicted, zero_division=0), 4),
    }
    try:
        result["auc"] = round(roc_auc_score(labels, scores), 4)
    except ValueError:
        result["auc"] = None
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=["maldonado", "tesoro"], default="tesoro")
    parser.add_argument("--input", choices=["text", "log", "code"], default="text",
                        help="which field to encode; log is only available for maldonado")
    parser.add_argument("--pair", choices=["none", "log", "code"], default="none",
                        help="encode comment_text and this field as a text pair")
    parser.add_argument("--csv", type=Path, default=None,
                        help="override the dataset path")
    parser.add_argument("--code-csv", type=Path, default=None,
                        help="code table for --pair code (default: MaldonadoSATD code table)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--split", default="random",
                        choices=["random", "duplicate-free", "project"])
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--fold", type=int, default=0,
                        help="1-based fold to run; 0 runs every fold")
    parser.add_argument("--epochs", type=float, default=3)
    parser.add_argument("--batch", type=int, default=32)
    parser.add_argument("--max-length", type=int, default=128)
    parser.add_argument("--lr", type=float, default=2e-5)
    parser.add_argument("--limit", type=int, default=0,
                        help="subsample this many training rows (smoke test)")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-predictions", type=Path, default=None)
    args = parser.parse_args()

    if args.csv is None:
        if args.dataset == "maldonado":
            args.csv = DEFAULT_MALDONADO_CODE if args.input == "code" else DEFAULT_MALDONADO
        else:
            args.csv = DEFAULT_TESORO
    if args.dataset == "maldonado":
        rows, texts, labels, projects = read_maldonado(args.csv, args.input)
    else:
        if args.input == "log":
            raise SystemExit("--input log is only available for maldonado")
        rows, texts, labels, projects = read_tesoro(args.csv, args.input)

    pairs = None
    input_label = args.input
    if args.pair != "none":
        if args.dataset == "tesoro" and args.pair == "log":
            raise SystemExit("--pair log is only available for maldonado")
        if args.input != "text":
            raise SystemExit("--pair is meant to be used with --input text")
        if args.pair == "log":
            pairs = [row["commit_log"] for row in rows]
        else:
            if args.dataset == "tesoro":
                pairs = [row.get("code") or row.get("code_context_2", "") for row in rows]
            else:
                code_path = args.code_csv or DEFAULT_MALDONADO_CODE
                opener = gzip.open if code_path.suffix == ".gz" else open
                with opener(code_path, "rt", encoding="utf-8", newline="") as handle:
                    code_rows = list(csv.DictReader(handle))
                code_by_id = {row["input_row"]: row["code"] for row in code_rows}
                pairs = [code_by_id[row["input_row"]] for row in rows]
        input_label = f"text+{args.pair}"

    print(f"dataset {args.dataset}; input {input_label}; rows {len(rows)}; "
          f"SATD {int(labels.sum())} ({100 * labels.mean():.1f}%); "
          f"model {args.model}", flush=True)

    if args.split == "project":
        if args.dataset != "maldonado":
            raise SystemExit("--split project is only available for maldonado")
        splits = list(LeaveOneGroupOut().split(texts, labels, projects))
    elif args.split == "duplicate-free":
        groups = np.array([" ".join(text.split()) for text in texts])
        splits = list(GroupKFold(args.folds).split(texts, labels, groups))
    else:
        splits = list(StratifiedKFold(args.folds, shuffle=True,
                                      random_state=args.seed).split(texts, labels))

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    encodings = tokenizer(texts, text_pair=pairs, truncation=True,
                          padding="max_length", max_length=args.max_length,
                          return_tensors="pt")
    results = []
    predictions = []
    selected = range(len(splits)) if args.fold == 0 else [args.fold - 1]
    for fold_index in selected:
        if not 0 <= fold_index < len(splits):
            raise SystemExit(f"fold {fold_index + 1} is outside 1..{len(splits)}")
        train, test = splits[fold_index]
        if args.limit:
            rng = np.random.default_rng(args.seed + fold_index)
            train = rng.choice(train, size=min(args.limit, len(train)), replace=False)
        random.seed(args.seed + fold_index + 1)
        np.random.seed(args.seed + fold_index + 1)
        torch.manual_seed(args.seed + fold_index + 1)

        train_set = Comments({key: value[train] for key, value in encodings.items()},
                             labels[train])
        test_set = Comments({key: value[test] for key, value in encodings.items()},
                            labels[test])
        positives = labels[train].sum()
        pos_weight = (len(train) - positives) / max(positives, 1)

        model = AutoModelForSequenceClassification.from_pretrained(
            args.model, num_labels=1, dtype=torch.float32)
        training = TrainingArguments(
            output_dir=f"/tmp/modernbert_satd_{fold_index + 1}",
            per_device_train_batch_size=args.batch,
            per_device_eval_batch_size=max(32, args.batch),
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            warmup_ratio=0.0,
            max_grad_norm=1.0,
            bf16=False,
            logging_steps=100,
            save_strategy="no",
            eval_strategy="no",
            report_to=[],
            seed=args.seed + fold_index + 1,
            disable_tqdm=False,
            dataloader_num_workers=0,
            use_mps_device=torch.backends.mps.is_available(),
        )
        trainer = WeightedTrainer(
            pos_weight=float(pos_weight), model=model, args=training,
            train_dataset=train_set, processing_class=tokenizer)
        trainer.train()
        logits = trainer.predict(test_set).predictions.reshape(-1)
        scores = 1.0 / (1.0 + np.exp(-logits))
        predictions.extend((fold_index + 1, int(index), int(label), float(score))
                           for index, label, score in zip(test, labels[test], scores))
        row = {"fold": fold_index + 1, "n_train": len(train), "n_test": len(test),
               **metrics(labels[test], scores)}
        results.append(row)
        print(json.dumps(row), flush=True)
        del model, trainer
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()

    if len(results) == 1:
        summary = {key: results[0][key] for key in ("precision", "recall", "f1", "auc")}
    else:
        summary = {key: round(float(np.mean([row[key] for row in results
                                             if row[key] is not None])), 4)
                   for key in ("precision", "recall", "f1", "auc")}
    payload = {
        "model": args.model, "dataset": args.dataset, "input": input_label,
        "split": args.split, "rows": len(rows), "satd": int(labels.sum()),
        "folds": len(results), "mean": summary, "per_fold": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if args.save_predictions:
        args.save_predictions.parent.mkdir(parents=True, exist_ok=True)
        with args.save_predictions.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["fold", "row", "label", "score"])
            writer.writerows(predictions)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
