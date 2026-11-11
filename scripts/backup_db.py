"""
backup_db.py
============
Make a consistent copy of the live database (safe while the web app is running,
because it uses SQLite's online backup API rather than copying the file).

    python scripts/backup_db.py            # -> instance/backups/study-YYYYmmdd-HHMMSS.db

Download the copy from the PythonAnywhere Files tab and keep it somewhere safe.
"""
import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()
    src_path = Path(args.db)
    if not src_path.exists():
        sys.exit(f"No database at {src_path}")
    out_dir = src_path.parent / "backups"
    out_dir.mkdir(exist_ok=True)
    out = out_dir / f"{src_path.stem}-{datetime.now():%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(str(src_path)), sqlite3.connect(str(out))
    with dst:
        src.backup(dst)
    counts = {t: dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("participants", "responses", "questionnaire")}
    src.close()
    dst.close()
    print(f"Backup written: {out}")
    print("  " + ", ".join(f"{t} {n}" for t, n in counts.items()))
