"""
db.py
=====
SQLite schema and connection helpers. `init_db()` only uses
CREATE TABLE IF NOT EXISTS, so it is safe to run again at any time.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
-- The 298 study headlines. Ground truth lives ONLY here and in server-side code.
CREATE TABLE IF NOT EXISTS trials (
    headline_id   TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    ground_truth  INTEGER NOT NULL,          -- 1 relevant, 0 not relevant
    ai_score      REAL NOT NULL,             -- precomputed score (used for sampling and as fallback)
    ai_label      INTEGER NOT NULL,          -- precomputed label at the threshold
    ai_correct    INTEGER NOT NULL,          -- ai_label == ground_truth
    ai_band       TEXT NOT NULL,
    tier          TEXT,
    region        TEXT,
    keyword       TEXT
);

CREATE TABLE IF NOT EXISTS participants (
    -- one row per SESSION: a person doing one version (baseline or ai) once
    id                         TEXT PRIMARY KEY,     -- random, unguessable
    uniqname_hash              TEXT NOT NULL,        -- HMAC of the uniqname; links a person's two versions
    uniqname                   TEXT,                 -- NULL if the participant chose to stay anonymous
    anonymous                  INTEGER NOT NULL,
    condition                  TEXT NOT NULL,        -- 'baseline' (no AI) or 'ai'
    attempt                    INTEGER NOT NULL,     -- 1, or 2 after a restart (per version)
    version_order              INTEGER NOT NULL,     -- 1 = first version this person started, 2 = second
    status                     TEXT NOT NULL,        -- active / completed / terminated / withdrawn
    attention_failures         INTEGER NOT NULL DEFAULT 0,
    instruction_check_answer   TEXT,
    instruction_check_correct  INTEGER,              -- NULL until answered
    consent_at                 TEXT NOT NULL,
    completed_at               TEXT,
    terminated_at              TEXT,
    withdrawn_at               TEXT,
    user_agent                 TEXT,
    completion_code            TEXT,
    final_feedback             TEXT
);
CREATE INDEX IF NOT EXISTS ix_participants_hash ON participants(uniqname_hash, condition);

CREATE TABLE IF NOT EXISTS assignments (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id      TEXT NOT NULL REFERENCES participants(id),
    headline_id         TEXT NOT NULL,       -- 'ATTENTION_CHECK' for the in-task check
    condition           TEXT NOT NULL,       -- 'baseline' or 'ai'
    position            INTEGER NOT NULL,    -- 1-based order in the session
    is_attention_check  INTEGER NOT NULL DEFAULT 0,
    first_served_at     TEXT,                -- when the trial was first sent to the browser
    ai_score_raw        REAL,                -- latest live AI output served for this trial
    ai_label            INTEGER,
    ai_band             TEXT,
    ai_fallback         INTEGER,
    ai_revealed_at      TEXT,                -- AI version: when "Show AI suggestion" was clicked
    UNIQUE (participant_id, position),
    UNIQUE (participant_id, headline_id)    -- no headline twice in a session
);

CREATE TABLE IF NOT EXISTS responses (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id           TEXT NOT NULL REFERENCES participants(id),
    assignment_id            INTEGER NOT NULL REFERENCES assignments(id),
    headline_id              TEXT NOT NULL,
    condition                TEXT NOT NULL,
    position                 INTEGER NOT NULL,
    is_attention_check       INTEGER NOT NULL,
    answer                   INTEGER NOT NULL,   -- 1 relevant, 0 not relevant
    correct                  INTEGER NOT NULL,   -- computed on the server
    confidence               INTEGER NOT NULL,   -- 1-5
    client_time_ms           INTEGER,            -- headline rendered -> Next clicked (browser clock)
    served_at                TEXT,
    server_received_at       TEXT NOT NULL,
    answer_changed_count     INTEGER,
    tab_hidden_during_trial  INTEGER,
    ai_revealed              INTEGER,            -- AI version: 1 if "Show AI suggestion" was clicked
    ai_reveal_ms             INTEGER,            -- ms from headline shown to that click
    ai_label_shown           INTEGER,            -- NULL unless the suggestion was shown
    ai_band_shown            TEXT,
    ai_score_raw             REAL,               -- live score (recorded in both versions)
    ai_label                 INTEGER,            -- live label (recorded in both versions)
    ai_correct               INTEGER,
    ai_fallback              INTEGER,
    explanation_opened       INTEGER,
    explanation_open_count   INTEGER,
    explanation_open_ms      INTEGER,
    UNIQUE (participant_id, position)
);

CREATE TABLE IF NOT EXISTS ai_calls (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id   TEXT,
    headline_id      TEXT,
    call_type        TEXT NOT NULL,      -- 'predict' or 'explain'
    condition        TEXT,               -- 'ai' (shown) or 'baseline' (hidden)
    model_name       TEXT,
    threshold        REAL,
    raw_score        REAL,
    label            INTEGER,
    band             TEXT,
    inference_ms     REAL,
    fallback         INTEGER NOT NULL DEFAULT 0,
    cached           INTEGER NOT NULL DEFAULT 0,
    error            TEXT,
    explanation_json TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id  TEXT,
    type            TEXT NOT NULL,
    payload         TEXT,               -- JSON
    client_time     TEXT,
    server_time     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS questionnaire (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id   TEXT NOT NULL UNIQUE REFERENCES participants(id),
    condition        TEXT NOT NULL,
    tlx_mental       INTEGER,
    tlx_effort       INTEGER,
    tlx_frustration  INTEGER,
    tlx_performance  INTEGER,   -- 1 = very unsuccessful ... 7 = very successful
    ai_trust         INTEGER,   -- AI version only
    ai_useful        INTEGER,   -- AI version only
    ai_why_helped    INTEGER,   -- AI version only; NULL = "didn't use it"
    comment          TEXT,
    submitted_at     TEXT NOT NULL
);
"""

# Tables that hold a participant's data (used by "delete my submission" and reset_db.py)
PARTICIPANT_TABLES = ["responses", "questionnaire", "events", "ai_calls", "assignments"]


def now_iso() -> str:
    """UTC timestamp with milliseconds, e.g. 2026-11-10T18:25:03.123Z"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def connect(path) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(path) -> None:
    conn = connect(path)
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(responses)")}
        if cols and "ai_revealed" not in cols:
            raise RuntimeError(
                f"{path} was created by an older version of the app. "
                "Delete that database file (or move it away) and run scripts/prepare_trials.py again.")
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
