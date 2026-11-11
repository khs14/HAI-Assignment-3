"""
sampling.py
===========
Picks the headlines for a new participant session. Everything is saved at
consent time, so a refresh resumes instead of resampling.

There are two separate study versions (two links), each a complete session:
  * baseline (no AI): 12 headlines, 6 relevant + 6 not relevant (ground truth)
  * ai (with AI):     12 headlines with exactly AI_ERRORS_PER_SESSION that the AI
                      gets wrong (drawn from the full error pool, so the natural
                      false-negative-heavy mix is kept) and the rest where the AI
                      is right, balanced to 6/6 by ground truth.

Other rules
-----------
* No headline twice in a session, and a person who already did the other
  version (same uniqname) never gets a headline they saw there.
* Less-used headlines are preferred: each headline is drawn with weight
  1 / (1 + times already assigned to a session that is not terminated/withdrawn).
* The in-task attention check goes at a random position in
  ATTENTION_POSITION_RANGE (so there are 13 items in total).
"""
from __future__ import annotations

import random
import sqlite3

import config


def headline_usage(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("""
        SELECT a.headline_id, COUNT(*) AS n
        FROM assignments a JOIN participants p ON p.id = a.participant_id
        WHERE a.is_attention_check = 0 AND p.status NOT IN ('terminated', 'withdrawn')
        GROUP BY a.headline_id""")
    return {r["headline_id"]: r["n"] for r in rows}


def seen_by_person(conn: sqlite3.Connection, uniqname_hash: str) -> set[str]:
    """Headlines this person was assigned in any earlier session (either version)."""
    rows = conn.execute("""
        SELECT a.headline_id FROM assignments a JOIN participants p ON p.id = a.participant_id
        WHERE p.uniqname_hash = ? AND a.is_attention_check = 0""", (uniqname_hash,))
    return {r["headline_id"] for r in rows}


def weighted_sample(ids: list[str], k: int, usage: dict[str, int], rng: random.Random) -> list[str]:
    """
    Weighted sampling without replacement (Efraimidis-Spirakis): give each item
    the key u ** (1 / w) with u ~ Uniform(0,1) and keep the k largest keys.
    Here w = 1 / (1 + uses), so 1 / w = 1 + uses.
    """
    if k <= 0:
        return []
    keyed = [(rng.random() ** (1.0 + usage.get(i, 0)), i) for i in ids]
    keyed.sort(reverse=True)
    return [i for _, i in keyed[:k]]


def _pick_balanced(pool_rel, pool_not, n_rel, n_not, usage, rng):
    """Pick n_rel from pool_rel and n_not from pool_not; if a pool is short, top up from the other."""
    short_rel = max(0, n_rel - len(pool_rel))
    short_not = max(0, n_not - len(pool_not))
    n_rel, n_not = n_rel - short_rel + short_not, n_not - short_not + short_rel
    return weighted_sample(pool_rel, n_rel, usage, rng) + weighted_sample(pool_not, n_not, usage, rng)


def sample_session(conn: sqlite3.Connection, condition: str, rng: random.Random,
                   exclude: set[str] = frozenset()) -> list[str]:
    """Return TRIALS_PER_SESSION shuffled headline ids for one session of `condition`."""
    n = config.TRIALS_PER_SESSION
    half = n // 2
    trials = [dict(r) for r in conn.execute("SELECT headline_id, ground_truth, ai_correct FROM trials")
              if r["headline_id"] not in exclude]
    if len(trials) < n:
        raise RuntimeError("Not enough headlines in the trials table. Run scripts/prepare_trials.py.")
    usage = headline_usage(conn)
    gt = {t["headline_id"]: t["ground_truth"] for t in trials}

    if condition == "ai":
        errors = [t["headline_id"] for t in trials if not t["ai_correct"]]
        chosen = weighted_sample(errors, config.AI_ERRORS_PER_SESSION, usage, rng)
        err_rel = sum(gt[h] == 1 for h in chosen)
        err_not = len(chosen) - err_rel
        right_rel = [t["headline_id"] for t in trials if t["ai_correct"] and t["ground_truth"] == 1]
        right_not = [t["headline_id"] for t in trials if t["ai_correct"] and t["ground_truth"] == 0]
        chosen += _pick_balanced(right_rel, right_not, max(0, half - err_rel),
                                 max(0, (n - half) - err_not), usage, rng)
    else:
        rel = [t["headline_id"] for t in trials if t["ground_truth"] == 1]
        nrel = [t["headline_id"] for t in trials if t["ground_truth"] == 0]
        chosen = _pick_balanced(rel, nrel, half, n - half, usage, rng)

    rng.shuffle(chosen)
    return chosen


def create_assignments(conn: sqlite3.Connection, participant_id: str, condition: str,
                       uniqname_hash: str, rng: random.Random) -> None:
    """Sample and INSERT all assignment rows for a session (caller commits)."""
    ids = sample_session(conn, condition, rng, exclude=seen_by_person(conn, uniqname_hash))
    items = [(h, 0) for h in ids]
    lo, hi = config.ATTENTION_POSITION_RANGE
    items.insert(rng.randint(lo, hi) - 1, (config.ATTENTION_HEADLINE_ID, 1))
    for position, (headline_id, is_attn) in enumerate(items, start=1):
        conn.execute(
            "INSERT INTO assignments (participant_id, headline_id, condition, position, "
            "is_attention_check) VALUES (?, ?, ?, ?, ?)",
            (participant_id, headline_id, condition, position, is_attn))
