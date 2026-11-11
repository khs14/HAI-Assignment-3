"""
check_model.py
==============
Phase 0 sanity check: does the saved logistic-regression model, loaded in THIS
Python environment, reproduce the scores in study_dataset_submission.csv?

Run it locally and again on PythonAnywhere after installing requirements.txt.

    python scripts/check_model.py
    python scripts/check_model.py --model-dir model_out --csv study_dataset_submission.csv

Exit code 0 = scores and labels match, 1 = mismatch (do not run the study).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from importlib import metadata
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))  # so relevance_model.py next to the app is importable

THRESHOLD = 0.80
SCORE_TOLERANCE = 1e-3  # the CSV stores scores rounded to 4 decimals


def installed(pkg: str) -> str:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return "not installed"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(APP_DIR / "model_out"))
    ap.add_argument("--csv", default=str(APP_DIR / "study_dataset_submission.csv"))
    args = ap.parse_args()

    # ---- 1. versions -------------------------------------------------------
    card = json.loads((Path(args.model_dir) / "model_card.json").read_text(encoding="utf-8"))
    trained = card.get("versions", {})
    print("Versions (trained -> installed)")
    print(f"  python        {trained.get('python', '?'):>10} -> {sys.version.split()[0]}")
    for pkg in ("numpy", "scikit-learn", "scipy", "pandas", "joblib"):
        print(f"  {pkg:<13} {trained.get(pkg, 'not recorded'):>10} -> {installed(pkg)}")

    # ---- 2. load model and score every study headline ----------------------
    from relevance_model import RelevanceClassifier

    clf = RelevanceClassifier.load(args.model_dir, model="logreg")
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    titles = [r["title"] for r in rows]

    t0 = time.perf_counter()
    preds = clf.predict(titles, threshold=THRESHOLD)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # ---- 3. compare against the CSV ----------------------------------------
    max_diff, label_mismatches = 0.0, []
    tp = tn = fp = fn = 0
    for r, p in zip(rows, preds):
        max_diff = max(max_diff, abs(p["score"] - float(r["model_score"])))
        if p["label"] != int(r["model_label"]):
            label_mismatches.append((r["id"], r["model_score"], p["score"]))
        gt = int(r["ground_truth"])
        if p["label"] == 1 and gt == 1: tp += 1
        elif p["label"] == 0 and gt == 0: tn += 1
        elif p["label"] == 1: fp += 1
        else: fn += 1

    n = len(rows)
    print(f"\nScored {n} headlines in {elapsed_ms:.0f} ms ({elapsed_ms / n:.2f} ms each, batched)")
    print(f"Max |score difference| vs CSV: {max_diff:.6f} (tolerance {SCORE_TOLERANCE})")
    print(f"Label mismatches vs CSV:       {len(label_mismatches)}")
    for m in label_mismatches:
        print(f"  {m[0]}: csv={m[1]} live={m[2]}")

    print(f"\nAt threshold {THRESHOLD}: errors {fp + fn} (FP {fp}, FN {fn}), "
          f"TP {tp}, TN {tn}, accuracy {(tp + tn) / n:.3f}")

    # ---- 4. score distribution ---------------------------------------------
    edges = [0.0, 0.05, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 1.0001]
    print("\nScore distribution (live scores)   all  gt=1  gt=0  errors")
    for lo, hi in zip(edges, edges[1:]):
        in_bin = [(r, p) for r, p in zip(rows, preds) if lo <= p["score"] < hi]
        pos = sum(int(r["ground_truth"]) == 1 for r, _ in in_bin)
        err = sum(int(r["ground_truth"]) != p["label"] for r, p in in_bin)
        print(f"  [{lo:.2f}, {min(hi, 1.0):.2f}){'':16}{len(in_bin):5d} {pos:5d} {len(in_bin) - pos:5d} {err:7d}")

    ok = max_diff <= SCORE_TOLERANCE and not label_mismatches
    print("\nRESULT:", "OK - live model matches the CSV" if ok else "MISMATCH - do not run the study")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
