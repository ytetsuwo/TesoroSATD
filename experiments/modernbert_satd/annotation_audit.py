#!/usr/bin/env python3
"""Audit false positives / false negatives of the best Tesoro model.

This is a screening pass, not a relabeling.  It tags errors by surface cues
(TODO/FIXME, uncertainty, design intent, defect language) and writes every
error to CSV so a human can review the candidates.
"""
from __future__ import annotations

import csv
import gzip
import json
import re
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RESULTS = ROOT / "experiments/modernbert_satd/results"
VIEWS = ROOT / "experiments/modernbert_satd/data/tesoro_views.csv.gz"
LARGE = RESULTS / "p1/modernbert-large_project_seed0.csv"

MARKER = re.compile(r"\b(TODO|FIXME|XXX|HACK|NOTE|ATTENTION)\b", re.I)
UNCERTAIN = re.compile(
    r"\b(maybe|perhaps|probably|might|may|not sure|unsure|confus\w*|"
    r"seems?|suppose|assume|expect|hope|why|\?\?)\b", re.I)
DESIGN = re.compile(
    r"\b(should|must|only|support|design|refactor|deprecat\w*|"
    r"workaround|temporary|assumption|intent\w*|trade-?off)\b", re.I)
DEFECT = re.compile(
    r"\b(bug|fix|wrong|error|fail\w*|broken|crash\w*|null|invalid|"
    r"race|deadlock|leak|overflow)\b", re.I)


def read_predictions(path):
    scores = {}
    labels = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            index = int(row["row"])
            scores[index] = float(row["score"])
            labels[index] = int(row["label"])
    return scores, labels


def main() -> int:
    with gzip.open(VIEWS, "rt", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    scores, labels = read_predictions(LARGE)
    errors = []
    for index, row in enumerate(rows):
        score = scores[index]
        label = labels[index]
        predicted = int(score >= 0.5)
        if predicted == label:
            continue
        text = row.get("comment_text", "")
        kind = "FP" if predicted == 1 else "FN"
        marker = bool(MARKER.search(text))
        uncertain = bool(UNCERTAIN.search(text))
        design = bool(DESIGN.search(text))
        defect = bool(DEFECT.search(text))
        if kind == "FP":
            if marker:
                category = "marker_in_nonsatd"
            elif uncertain:
                category = "uncertainty_in_nonsatd"
            elif design:
                category = "design_cue_in_nonsatd"
            elif defect:
                category = "defect_cue_in_nonsatd"
            else:
                category = "no_obvious_cue"
        else:
            if design:
                category = "implicit_design"
            elif defect:
                category = "implicit_defect"
            elif uncertain:
                category = "implicit_uncertainty"
            else:
                category = "no_marker_or_cue"
        errors.append({
            "kind": kind,
            "row": index,
            "project": row.get("project_name", ""),
            "classification": row.get("classification", ""),
            "label": label,
            "score": round(score, 4),
            "words": len(text.split()),
            "marker": marker,
            "uncertainty": uncertain,
            "design": design,
            "defect": defect,
            "category": category,
            "comment": text[:500],
        })

    summary = {
        "errors": len(errors),
        "fp": sum(1 for e in errors if e["kind"] == "FP"),
        "fn": sum(1 for e in errors if e["kind"] == "FN"),
        "categories": {},
        "samples": {},
    }
    for kind in ("FP", "FN"):
        subset = [e for e in errors if e["kind"] == kind]
        counts = {}
        for entry in subset:
            counts[entry["category"]] = counts.get(entry["category"], 0) + 1
        summary["categories"][kind] = counts
        summary["samples"][kind] = subset[:10]

    output = RESULTS / "p3_annotation_audit.json"
    output.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    csv_path = RESULTS / "p3_annotation_audit.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(errors[0]))
        writer.writeheader()
        writer.writerows(errors)

    print(json.dumps({key: summary[key] for key in ("errors", "fp", "fn", "categories")},
                     indent=2))
    print(f"wrote {output} and {csv_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
