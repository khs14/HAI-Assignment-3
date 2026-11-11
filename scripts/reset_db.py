"""
reset_db.py
===========
Clear all participant data (test runs, simulations) before the real study.
Makes a timestamped backup copy of the database first. The `trials` table is kept.

    python scripts/reset_db.py --yes
"""
import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import db      # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    ap.add_argument("--yes", action="store_true", help="really delete participant data")
    args = ap.parse_args()
    path = Path(args.db)
    if not args.yes:
        sys.exit("Refusing to run without --yes (this deletes all participant data).")
    if path.exists():
        backup_dir = path.parent / "backups"
        backup_dir.mkdir(exist_ok=True)
        backup = backup_dir / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}.db"
        shutil.copy2(path, backup)
        print(f"Backup written: {backup}")
    db.init_db(path)
    conn = db.connect(path)
    for table in db.PARTICIPANT_TABLES + ["participants"]:
        n = conn.execute(f"DELETE FROM {table}").rowcount
        print(f"  cleared {table:<14} ({n} rows)")
    conn.execute("DELETE FROM sqlite_sequence")
    conn.commit()
    conn.execute("VACUUM")
    print("Done. trials table kept:", conn.execute("SELECT COUNT(*) FROM trials").fetchone()[0], "rows")
