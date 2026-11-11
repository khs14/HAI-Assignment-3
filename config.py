"""
config.py
=========
Every study setting that you might want to change lives here, in one place.

Secrets (Flask secret key, admin password, uniqname hashing key) are NOT stored
here. They come from environment variables, or from instance/secrets.json, which
is gitignored. See DEPLOY.md.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ---- Files -----------------------------------------------------------------
INSTANCE_DIR = Path(os.environ.get("STUDY_INSTANCE_DIR", BASE_DIR / "instance"))
DB_PATH = Path(os.environ.get("STUDY_DB_PATH", INSTANCE_DIR / "study.db"))
STUDY_CSV = BASE_DIR / "study_dataset_submission.csv"
MODEL_DIR = BASE_DIR / "model_out"
TUTORIAL_FILE = BASE_DIR / "tutorial_examples.json"

# ---- Model -----------------------------------------------------------------
MODEL_NAME = "logreg"
THRESHOLD = 0.80          # score >= THRESHOLD  ->  "Relevant"

# Confidence-band cut points. They are applied AFTER the label is decided, so a
# band can never contradict the label.
#   Relevant      (score >= THRESHOLD): High if score >= RELEVANT_HIGH, else Medium
#   Not relevant  (score <  THRESHOLD): High if score <  NOT_RELEVANT_HIGH,
#                                       Medium if score < NOT_RELEVANT_MEDIUM, else Low
RELEVANT_HIGH = 0.95
NOT_RELEVANT_HIGH = 0.20
NOT_RELEVANT_MEDIUM = 0.50

SHOW_SCORE = False        # also show the raw score as a percentage in the AI box?

# ---- "Why?" explanation (leave-one-word-out occlusion) -----------------------
EXPLAIN_TOP_K = 3         # words shown per direction
EXPLAIN_MIN_DELTA = 0.001 # ignore words whose removal changes the score less than this

# ---- Study design ----------------------------------------------------------
# Two separate study versions, each at its own link: /baseline and /ai.
CONDITIONS = ("baseline", "ai")
CONDITION_TEXT = {"baseline": "Without AI", "ai": "With AI"}
# 12 keeps both versions well under the 10-minute limit
# (estimated ~5 min without AI, ~7 min with AI; check real times on /admin).
TRIALS_PER_SESSION = 12            # real headlines per session (plus 1 attention check)
SESSION_TIME_LIMIT_MIN = 10        # course limit; /admin flags sessions that took longer
AI_ERRORS_PER_SESSION = 2          # AI version: headlines where the AI is wrong
ATTENTION_POSITION_RANGE = (4, 9)  # in-task check goes at one of these positions (1-based)
# Attention checks: a first miss shows a warning and the SAME check again;
# a second miss ends the session, and that uniqname can't take that version again.
MAX_ATTENTION_FAILURES = 2
MAX_ATTEMPTS_PER_VERSION = 1       # no restart after termination
UNIQNAME_PATTERN = r"^[a-z]{3,8}$" # UMich uniqnames: 3-8 lowercase letters

ATTENTION_HEADLINE_ID = "ATTENTION_CHECK"
ATTENTION_TEXT = "This is an attention check. Please select Not relevant."
INSTRUCTION_CHECK_CORRECT = "not_relevant"

LABEL_NAMES = {0: "not_relevant", 1: "relevant"}
LABEL_TEXT = {0: "Not relevant", 1: "Relevant"}


def label_for(score: float, threshold: float = THRESHOLD) -> int:
    return int(score >= threshold)


def band_for(score: float, threshold: float = THRESHOLD) -> str:
    """Confidence band for a score; never contradicts the label at `threshold`."""
    if label_for(score, threshold) == 1:
        return "High" if score >= RELEVANT_HIGH else "Medium"
    if score < NOT_RELEVANT_HIGH:
        return "High"
    if score < NOT_RELEVANT_MEDIUM:
        return "Medium"
    return "Low"


def check_band_config(threshold: float = THRESHOLD) -> None:
    """Fail loudly if the cut points are in an impossible order."""
    assert 0 < NOT_RELEVANT_HIGH < NOT_RELEVANT_MEDIUM <= threshold < RELEVANT_HIGH < 1, (
        "Band cut points must satisfy 0 < NOT_RELEVANT_HIGH < NOT_RELEVANT_MEDIUM "
        "<= THRESHOLD < RELEVANT_HIGH < 1")


# ---- Secrets ---------------------------------------------------------------
SECRET_NAMES = {
    "SECRET_KEY": "STUDY_SECRET_KEY",          # signs the session cookie
    "ADMIN_PASSWORD": "STUDY_ADMIN_PASSWORD",  # /admin login
    "UNIQNAME_KEY": "STUDY_UNIQNAME_KEY",      # HMAC key for hashing uniqnames (never change mid-study)
}


def load_secrets() -> dict:
    """Environment variables win; otherwise instance/secrets.json."""
    file_values = {}
    secrets_file = INSTANCE_DIR / "secrets.json"
    if secrets_file.exists():
        file_values = json.loads(secrets_file.read_text(encoding="utf-8"))
    out = {}
    for key, env_name in SECRET_NAMES.items():
        value = os.environ.get(env_name) or file_values.get(key)
        if not value:
            raise RuntimeError(
                f"Missing secret {key}: set the {env_name} environment variable "
                f"or add \"{key}\" to {secrets_file}")
        out[key] = value
    return out
