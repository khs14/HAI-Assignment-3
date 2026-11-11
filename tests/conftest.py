"""
Shared test fixtures.

The tests never touch the private study CSV: they build a synthetic `trials`
table with the same shape (298 headlines, 140 relevant, 42 AI errors of which
38 are false negatives). The real model is still loaded, so live prediction and
"Why?" explanations run for real on the fake titles.
"""
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import db      # noqa: E402
from ai_service import AIService  # noqa: E402
from app import create_app  # noqa: E402

SECRETS = {"SECRET_KEY": "test-secret", "ADMIN_PASSWORD": "admin-pw", "UNIQNAME_KEY": "test-key"}

# Unique marker so the leak test can search for ground truth text in responses.
GT_MARKER = "GTSECRET"


def make_trials(conn, seed=0):
    """298 synthetic headlines: 102 TP, 154 TN, 38 FN, 4 FP (same mix as the study set)."""
    rng = random.Random(seed)
    groups = [("TP", 1, 1, 102), ("TN", 0, 0, 154), ("FN", 1, 0, 38), ("FP", 0, 1, 4)]
    i = 0
    for name, gt, ai_label, n in groups:
        for _ in range(n):
            i += 1
            score = rng.uniform(0.81, 0.99) if ai_label else rng.uniform(0.01, 0.79)
            title = (f"City expands solar power and recycling program number {i}" if gt
                     else f"Local team wins basketball game number {i}")
            conn.execute(
                "INSERT INTO trials (headline_id, title, ground_truth, ai_score, ai_label, ai_correct, "
                "ai_band, tier, region, keyword) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (f"T{i:03d}", title, gt, score, ai_label, int(gt == ai_label),
                 config.band_for(score), f"{GT_MARKER}-tier", "US", f"{GT_MARKER}-kw"))
    conn.commit()


@pytest.fixture(scope="session")
def ai_service():
    svc = AIService()
    assert svc.clf is not None, svc.load_error
    return svc


@pytest.fixture
def app(tmp_path, ai_service):
    path = tmp_path / "test.db"
    db.init_db(path)
    conn = db.connect(path)
    make_trials(conn)
    conn.close()
    application = create_app({**SECRETS, "DB_PATH": path, "TESTING": True,
                              "AI_SERVICE": ai_service, "RNG": random.Random(1234)})
    yield application


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def conn(app):
    c = db.connect(app.config["DB_PATH"])
    yield c
    c.close()


# ---------------------------------------------------------------- helpers
def start(client, cond="ai", uniqname="abcde", anonymous=True):
    r = client.post(f"/api/{cond}/start", json={"consent": True, "uniqname": uniqname,
                                                "anonymous": anonymous})
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def pass_instruction_check(client, cond="ai", correct=True):
    r = client.post(f"/api/{cond}/attention", json={"answer": "not_relevant" if correct else "relevant"})
    assert r.status_code == 200
    return r.get_json()


def answer_current(client, cond="ai", answer=None, confidence=3):
    """Fetch the next trial and answer it. Attention checks are answered correctly unless answer given."""
    trial = client.get(f"/api/{cond}/trial/next").get_json()
    assert trial["stage"] == "trials", trial
    if answer is None:
        answer = 0 if trial["is_attention_check"] else 1
    r = client.post(f"/api/{cond}/trial/answer", json={
        "position": trial["position"], "answer": answer, "confidence": confidence,
        "client_time_ms": 1500})
    return trial, r


def finish_trials(client, cond="ai"):
    while True:
        st = client.get(f"/api/{cond}/state").get_json()
        if st["stage"] != "trials":
            return st
        _, r = answer_current(client, cond)
        assert r.status_code == 200, r.get_json()


def submit_questionnaire(client, cond="ai"):
    payload = {"tlx_mental": 3, "tlx_effort": 3, "tlx_frustration": 2, "tlx_performance": 5}
    if cond == "ai":
        payload.update(ai_trust=5, ai_useful=5, ai_why_helped=None, comment="ok")
    r = client.post(f"/api/{cond}/questionnaire", json=payload)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def complete_session(client, cond="ai", uniqname="abcde"):
    start(client, cond, uniqname)
    pass_instruction_check(client, cond)
    st = finish_trials(client, cond)
    assert st["stage"] == "questionnaire"
    return submit_questionnaire(client, cond)
