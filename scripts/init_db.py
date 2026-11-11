"""
init_db.py
==========
Create every table in the SQLite database. Safe to re-run: it only creates
tables that do not exist yet and never deletes data.

    python scripts/init_db.py                 # uses config.DB_PATH (instance/study.db)
    python scripts/init_db.py --db other.db
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402
import db      # noqa: E402

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()
    db.init_db(args.db)
    conn = db.connect(args.db)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                         "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    print(f"Database ready: {args.db}")
    for t in tables:
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:<14} {n:6d} rows")
