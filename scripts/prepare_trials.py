"""
prepare_trials.py
=================
Load the study CSV (study_dataset_submission.csv) into the `trials` table.

    python scripts/prepare_trials.py
    python scripts/prepare_trials.py --csv study_dataset_submission.csv --db instance/study.db

For each headline it stores: id, title, ground truth, precomputed AI score, AI
label at config.THRESHOLD, ai_correct, confidence band (config cut points), and
tier / region / keyword.

Safe to re-run (rows are upserted by headline_id). Re-run it after changing the
threshold or band cut points in config.py.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import db      # noqa: E402

REQUIRED = ["id", "title", "ground_truth", "model_score"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(config.STUDY_CSV))
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()

    config.check_band_config()
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        print(f"ERROR: CSV is missing columns {missing}. Found: {list(rows[0])}")
        return 1

    db.init_db(args.db)
    conn = db.connect(args.db)
    bands, warnings = Counter(), []
    for r in rows:
        score = float(r["model_score"])
        label = config.label_for(score)
        gt = int(r["ground_truth"])
        band = config.band_for(score)
        if "model_label" in r and int(r["model_label"]) != label:
            warnings.append(f"{r['id']}: CSV model_label {r['model_label']} != label at "
                            f"{config.THRESHOLD} ({label}); using {label}")
        bands[(config.LABEL_TEXT[label], band)] += 1
        conn.execute("""
            INSERT INTO trials (headline_id, title, ground_truth, ai_score, ai_label, ai_correct,
                                ai_band, tier, region, keyword)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(headline_id) DO UPDATE SET
                title=excluded.title, ground_truth=excluded.ground_truth,
                ai_score=excluded.ai_score, ai_label=excluded.ai_label,
                ai_correct=excluded.ai_correct, ai_band=excluded.ai_band,
                tier=excluded.tier, region=excluded.region, keyword=excluded.keyword""",
            (r["id"], r["title"].strip(), gt, score, label, int(label == gt), band,
             r.get("tier"), r.get("region"), r.get("keyword")))
    conn.commit()

    # tutorial examples must not come from the study set
    if config.TUTORIAL_FILE.exists():
        titles = {r["title"].strip().lower() for r in rows}
        for ex in json.loads(config.TUTORIAL_FILE.read_text(encoding="utf-8"))["examples"]:
            if ex["title"].strip().lower() in titles:
                warnings.append(f"Tutorial example is in the study set: {ex['title']}")

    t = conn.execute("""SELECT COUNT(*) n, SUM(ground_truth) rel, SUM(1 - ai_correct) err,
                        SUM(CASE WHEN ai_correct=0 AND ground_truth=1 THEN 1 ELSE 0 END) fn,
                        SUM(CASE WHEN ai_correct=0 AND ground_truth=0 THEN 1 ELSE 0 END) fp
                        FROM trials""").fetchone()
    print(f"Loaded {len(rows)} rows into {args.db}")
    print(f"trials table: {t['n']} headlines, {t['rel']} relevant / {t['n'] - t['rel']} not relevant")
    print(f"AI errors at {config.THRESHOLD}: {t['err']} (FN {t['fn']}, FP {t['fp']})")
    print("Bands:")
    for (lab, band), n in sorted(bands.items()):
        print(f"  {lab:<13} {band:<7} {n:4d}")
    for w in warnings:
        print("WARNING:", w)
    return 0


if __name__ == "__main__":
    sys.exit(main())
