"""
pytest tests for the study server.  Run:  python -m pytest -q
"""
import json
import random
from collections import Counter

import pytest

import config
import sampling
from conftest import (GT_MARKER, answer_current, complete_session, finish_trials,
                      pass_instruction_check, start, submit_questionnaire)

GT_KEYS = {"ground_truth", "ground_truth_name", "correct", "correct_label", "correct_label_text",
           "your_answer_correct", "ai_correct", "ai_was_correct", "tier", "keyword"}
CONDS = ("baseline", "ai")


def all_keys(obj):
    """Every dict key anywhere inside a JSON value."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


def assignments_of(conn, pid):
    return [dict(r) for r in conn.execute(
        "SELECT a.*, t.ground_truth, t.ai_correct FROM assignments a "
        "LEFT JOIN trials t ON t.headline_id = a.headline_id "
        "WHERE participant_id=? ORDER BY position", (pid,))]


def new_client(client):
    return client.application.test_client()


# =========================================================================== #
# Pages
# =========================================================================== #
def test_two_separate_links(client):
    assert client.get("/").status_code == 200
    base = client.get("/baseline").get_data(as_text=True)
    ai = client.get("/ai").get_data(as_text=True)
    assert 'data-condition="baseline"' in base and "Without AI" in base
    assert 'data-condition="ai"' in ai and "About the AI" in ai
    assert "About the AI" not in base
    assert client.get("/nonsense").status_code == 404
    assert client.get("/api/nonsense/state").status_code == 404


# =========================================================================== #
# Sampling
# =========================================================================== #
@pytest.mark.parametrize("cond", CONDS)
def test_session_size_balance_and_ai_errors(client, conn, cond):
    for i in range(12):
        start(new_client(client), cond, uniqname=f"user{chr(97 + i)}")
    for (pid,) in conn.execute("SELECT id FROM participants"):
        rows = assignments_of(conn, pid)
        real = [r for r in rows if not r["is_attention_check"]]
        attn = [r for r in rows if r["is_attention_check"]]
        assert len(real) == 12 and len(attn) == 1
        assert len({r["headline_id"] for r in rows}) == 13             # no repeats
        assert 4 <= attn[0]["position"] <= 9
        assert [r["position"] for r in rows] == list(range(1, 14))
        assert {r["condition"] for r in rows} == {cond}
        assert sum(r["ground_truth"] for r in real) == 6               # 6/6 by ground truth
        if cond == "ai":
            assert sum(1 - r["ai_correct"] for r in real) == 2          # exactly 2 AI errors


def test_same_person_never_sees_a_headline_twice_across_versions(client, conn):
    c = new_client(client)
    start(c, "baseline", "twice")
    start(c, "ai", "twice")
    rows = conn.execute("SELECT id, condition, version_order FROM participants").fetchall()
    order = {r["condition"]: r["version_order"] for r in rows}
    assert order == {"baseline": 1, "ai": 2}
    seen = [set(a["headline_id"] for a in assignments_of(conn, r["id"]) if not a["is_attention_check"])
            for r in rows]
    assert not (seen[0] & seen[1])


def test_less_used_headlines_are_preferred(conn, app):
    ids = [r[0] for r in conn.execute("SELECT headline_id FROM trials")]
    usage = {h: 50 for h in ids[:149]}
    rng = random.Random(0)
    picks = Counter()
    for _ in range(200):
        picks.update(sampling.weighted_sample(ids, 12, usage, rng))
    heavy = sum(picks[h] for h in ids[:149])
    light = sum(picks[h] for h in ids[149:])
    assert light > 5 * heavy


# =========================================================================== #
# No ground-truth leak
# =========================================================================== #
@pytest.mark.parametrize("cond", CONDS)
def test_no_ground_truth_before_answer(client, cond):
    """Call every participant endpoint and check no ground-truth field appears before answering."""
    seen = []

    def record(resp, before_answer=True, allowed=()):
        text = resp.get_data(as_text=True)
        if before_answer:
            assert GT_MARKER not in text
            if resp.is_json:
                leaked = (GT_KEYS - set(allowed)) & set(all_keys(resp.get_json()))
                assert not leaked, (resp.request.path, leaked)
        seen.append(resp.request.path.replace(f"/api/{cond}", ""))
        return resp

    api = f"/api/{cond}"
    record(client.get(f"/{cond}"))
    record(client.get(f"{api}/state"))
    record(client.post(f"{api}/start", json={"consent": True, "uniqname": "leaky", "anonymous": True}))
    record(client.post(f"{api}/event", json={"type": "instructions_opened"}))
    # the instruction check is not a study headline; its own result may be returned
    record(client.post(f"{api}/attention", json={"answer": "not_relevant"}), allowed={"correct"})

    while True:
        st = record(client.get(f"{api}/state")).get_json()
        if st["stage"] == "questionnaire":
            record(client.post(f"{api}/questionnaire", json={
                "tlx_mental": 3, "tlx_effort": 3, "tlx_frustration": 3, "tlx_performance": 3,
                "ai_trust": 3, "ai_useful": 3}))
            continue
        if st["stage"] != "trials":
            break
        trial = record(client.get(f"{api}/trial/next")).get_json()
        record(client.get(f"{api}/trial/next"))   # refresh: same trial, still no leak
        assert trial["ai"] is None                            # never sent with the headline
        if cond == "ai" and not trial["is_attention_check"]:
            sug = record(client.post(f"{api}/suggest", json={"position": trial["position"]})).get_json()
            assert set(sug) <= {"ok", "label", "label_text", "band", "score_pct"}
            assert "score_pct" not in sug                     # SHOW_SCORE defaults to off
            record(client.post(f"{api}/explain", json={"position": trial["position"]}))
        r = client.post(f"{api}/trial/answer", json={
            "position": trial["position"], "answer": 0, "confidence": 3})
        record(r, before_answer=False)                        # feedback is revealed here, by design
        assert r.get_json()["correct_label"] in ("relevant", "not_relevant")

    expected = {"/start", "/trial/next", "/attention", "/questionnaire", "/event"}
    assert expected <= set(seen)
    if cond == "ai":
        assert {"/suggest", "/explain"} <= set(seen)


def test_review_and_results_only_after_completion(client):
    start(client)
    pass_instruction_check(client)
    assert client.get("/api/ai/review").status_code == 409
    assert client.get("/api/ai/results").status_code == 409


def test_explain_refused_in_baseline(client):
    start(client, "baseline")
    pass_instruction_check(client, "baseline")
    trial = client.get("/api/baseline/trial/next").get_json()
    r = client.post("/api/baseline/explain", json={"position": trial["position"]})
    assert r.status_code == 403


# =========================================================================== #
# Answer validation
# =========================================================================== #
def test_duplicate_and_out_of_order_rejected(client):
    start(client)
    pass_instruction_check(client)
    trial = client.get("/api/ai/trial/next").get_json()
    body = {"position": trial["position"], "answer": 1, "confidence": 4}
    r = client.post("/api/ai/trial/answer", json={**body, "position": trial["position"] + 1})
    assert r.status_code == 409 and r.get_json()["error"] == "out_of_order"
    assert client.post("/api/ai/trial/answer", json=body).status_code == 200
    r = client.post("/api/ai/trial/answer", json=body)
    assert r.status_code == 409 and r.get_json()["error"] == "duplicate"


def test_answer_before_serving_rejected(client):
    start(client)
    pass_instruction_check(client)
    r = client.post("/api/ai/trial/answer", json={"position": 1, "answer": 1, "confidence": 3})
    assert r.status_code == 409 and r.get_json()["error"] == "not_served"


def test_trials_locked_until_instruction_check(client):
    start(client)
    assert client.get("/api/ai/trial/next").get_json()["stage"] == "tutorial"


def test_bad_inputs_rejected(client):
    assert client.post("/api/ai/start", json={"consent": False, "uniqname": "abcde"}).status_code == 400
    assert client.post("/api/ai/start", json={"consent": True, "uniqname": "a@b"}).status_code == 400
    start(client)
    pass_instruction_check(client)
    trial = client.get("/api/ai/trial/next").get_json()
    r = client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 1,
                                                  "confidence": 9})
    assert r.status_code == 400


def test_sessions_of_two_versions_are_independent(client):
    """One browser can be mid-way through both versions; each keeps its own place."""
    start(client, "baseline", "both")
    pass_instruction_check(client, "baseline")
    answer_current(client, "baseline")
    start(client, "ai", "both")
    assert client.get("/api/ai/state").get_json()["stage"] == "tutorial"
    assert client.get("/api/baseline/state").get_json() == {"ok": True, "stage": "trials", "answered": 1}


# =========================================================================== #
# Attention checks and termination
# =========================================================================== #
def go_to_attention_trial(client, cond="ai"):
    while True:
        trial = client.get(f"/api/{cond}/trial/next").get_json()
        if trial["is_attention_check"]:
            return trial
        client.post(f"/api/{cond}/trial/answer", json={"position": trial["position"], "answer": 1,
                                                       "confidence": 3})


def test_instruction_check_retry_then_terminate_for_good(client, conn):
    start(client, "ai", "failer")
    r = pass_instruction_check(client, correct=False)
    assert r["correct"] is False and r["retry"] is True and r["terminated"] is False
    assert client.get("/api/ai/state").get_json()["stage"] == "tutorial"     # same check again
    r = pass_instruction_check(client, correct=False)
    assert r["terminated"] is True
    st = client.get("/api/ai/state").get_json()
    assert st["stage"] == "terminated" and st["can_restart"] is False
    assert client.post("/api/ai/restart", json={}).status_code == 409
    # the same uniqname can't take this version again, from any browser
    r = new_client(client).post("/api/ai/start", json={"consent": True, "uniqname": "failer"})
    assert r.status_code == 409 and r.get_json()["error"] == "terminated"
    # the other version is still allowed
    start(new_client(client), "baseline", "failer")


def test_instruction_check_retry_then_pass(client, conn):
    start(client, "ai", "retrier")
    pass_instruction_check(client, correct=False)
    assert pass_instruction_check(client, correct=True)["correct"] is True
    row = conn.execute("SELECT * FROM participants").fetchone()
    assert row["instruction_check_answer"] == "relevant,not_relevant"
    assert row["instruction_check_correct"] == 1 and row["attention_failures"] == 1
    assert client.get("/api/ai/state").get_json()["stage"] == "trials"


def test_in_task_check_is_repeated_after_a_miss(client, conn):
    start(client)
    pass_instruction_check(client)
    trial = go_to_attention_trial(client)
    r = client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 1,
                                                  "confidence": 3}).get_json()
    assert r["retry"] is True and r["terminated"] is False
    again = client.get("/api/ai/trial/next").get_json()
    assert again["is_attention_check"] and again["position"] == trial["position"]
    r = client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 0,
                                                  "confidence": 3}).get_json()
    assert r["your_answer_correct"] is True
    nxt = client.get("/api/ai/trial/next").get_json()
    assert nxt["position"] == trial["position"] + 1
    assert conn.execute("SELECT COUNT(*) FROM events WHERE type='attention_check_failed'").fetchone()[0] == 1


def test_one_miss_in_each_check_terminates(client, conn):
    """Failures add up: a miss on the instruction check plus a miss on the in-task check ends it."""
    start(client, "ai", "twomiss")
    pass_instruction_check(client, correct=False)
    pass_instruction_check(client, correct=True)
    trial = go_to_attention_trial(client)
    r = client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 1,
                                                  "confidence": 3}).get_json()
    assert r["terminated"] is True
    assert client.get("/api/ai/state").get_json()["stage"] == "terminated"
    assert conn.execute("SELECT status FROM participants").fetchone()[0] == "terminated"


def test_passing_both_checks_first_time(client):
    start(client)
    pass_instruction_check(client)
    trial = go_to_attention_trial(client)
    r = client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 0,
                                                  "confidence": 3}).get_json()
    assert r["terminated"] is False and r["your_answer_correct"] is True


# =========================================================================== #
# Synchronous AI and fallback
# =========================================================================== #
@pytest.mark.parametrize("cond", CONDS)
def test_live_prediction_logged_for_every_trial(client, conn, cond):
    start(client, cond)
    pass_instruction_check(client, cond)
    trial = client.get(f"/api/{cond}/trial/next").get_json()
    if trial["is_attention_check"]:
        return
    row = conn.execute("SELECT * FROM ai_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["headline_id"] == trial["headline_id"] and row["fallback"] == 0
    assert row["model_name"] == "logreg" and row["threshold"] == 0.8
    assert row["inference_ms"] is not None and row["condition"] == cond


def test_fallback_when_live_prediction_raises(client, conn, app, monkeypatch):
    svc = app.extensions["ai"]

    def boom(*a, **k):
        raise RuntimeError("model exploded")
    monkeypatch.setattr(svc.clf, "predict", boom)
    start(client)
    pass_instruction_check(client)
    trial = client.get("/api/ai/trial/next").get_json()      # must not crash
    if trial["is_attention_check"]:
        client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 0,
                                                  "confidence": 3})
        trial = client.get("/api/ai/trial/next").get_json()
    sug = client.post("/api/ai/suggest", json={"position": trial["position"]}).get_json()
    assert sug["label"] in ("relevant", "not_relevant")       # participant still gets an answer
    row = conn.execute("SELECT * FROM ai_calls ORDER BY id DESC LIMIT 1").fetchone()
    assert row["call_type"] == "suggest"
    pre = conn.execute("SELECT ai_score FROM trials WHERE headline_id=?", (trial["headline_id"],)).fetchone()
    assert row["fallback"] == 1 and "model exploded" in row["error"]
    assert row["raw_score"] == pytest.approx(pre["ai_score"])
    r = client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 1,
                                                  "confidence": 3})
    assert r.status_code == 200
    assert conn.execute("SELECT ai_fallback FROM responses").fetchone()[0] == 1


def test_explanation_words_come_from_headline(ai_service):
    title = "Delhi air pollution worsens as stubble burning rises, says Miami Heat coach"
    res = ai_service.explain("X1", title)
    words = title.split()
    assert res["error"] is None and res["toward_relevant"]
    for item in res["toward_relevant"] + res["toward_not_relevant"]:
        assert words[item["index"]] == item["word"]
    assert len(res["toward_relevant"]) <= 3 and len(res["toward_not_relevant"]) <= 3
    assert ai_service.explain("X1", title)["cached"] is True


def test_bands_never_contradict_label():
    for i in range(1001):
        s = i / 1000
        band, label = config.band_for(s), config.label_for(s)
        assert band in (("High", "Medium") if label == 1 else ("High", "Medium", "Low"))


# =========================================================================== #
# Resume, uniqname rules, completion, review, withdraw
# =========================================================================== #
def test_resume_after_refresh(client, conn):
    start(client, "ai", "resumer")
    pass_instruction_check(client)
    for _ in range(3):
        answer_current(client)
    pid = conn.execute("SELECT id FROM participants").fetchone()[0]
    before = assignments_of(conn, pid)
    t1 = client.get("/api/ai/trial/next").get_json()
    t2 = client.get("/api/ai/trial/next").get_json()          # "refresh"
    assert (t1["position"], t1["headline_id"]) == (t2["position"], t2["headline_id"])
    other = new_client(client)                                # new browser, same uniqname
    r = other.post("/api/ai/start", json={"consent": True, "uniqname": "resumer"}).get_json()
    assert r["resumed"] is True
    assert other.get("/api/ai/trial/next").get_json()["headline_id"] == t1["headline_id"]
    assert [a["headline_id"] for a in before] == [a["headline_id"] for a in assignments_of(conn, pid)]
    assert conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0] == 1


def test_anonymous_vs_named_storage(client, conn):
    start(client, "ai", "anonuser", anonymous=True)
    start(new_client(client), "ai", "named", anonymous=False)
    rows = {r["anonymous"]: r for r in conn.execute("SELECT * FROM participants")}
    assert rows[1]["uniqname"] is None and len(rows[1]["uniqname_hash"]) == 64
    assert rows[0]["uniqname"] == "named"
    assert "anonuser" not in json.dumps([dict(r) for r in conn.execute("SELECT * FROM participants")])


def test_complete_both_versions_review_results_then_withdraw(client, conn):
    st = complete_session(client, "baseline", "finisher")
    assert st["stage"] == "completed" and st["completion_code"].startswith("NOAI-")
    st = complete_session(client, "ai", "finisher")
    assert st["completion_code"].startswith("AI-")
    review = client.get("/api/ai/review").get_json()
    assert len(review["responses"]) == 13 and review["questionnaire"]["ai_trust"] == 5
    results = client.get("/api/ai/results").get_json()
    assert results["you"]["n"] == 12 and results["ai"]["people"] == 1 and results["baseline"]["people"] == 1
    # can't redo a version
    again = new_client(client).post("/api/ai/start", json={"consent": True, "uniqname": "finisher"})
    assert again.status_code == 409
    # delete the AI submission only
    assert client.post("/api/ai/withdraw", json={}).status_code == 400
    assert client.post("/api/ai/withdraw", json={"confirm": True}).get_json()["stage"] == "withdrawn"
    ai_pid = conn.execute("SELECT id FROM participants WHERE condition='ai'").fetchone()[0]
    for table in ("responses", "assignments", "questionnaire", "events", "ai_calls"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE participant_id=?", (ai_pid,)).fetchone()[0] == 0
    assert client.get("/api/baseline/state").get_json()["stage"] == "completed"
    again = new_client(client).post("/api/ai/start", json={"consent": True, "uniqname": "finisher"})
    assert again.status_code == 409


# =========================================================================== #
# Admin
# =========================================================================== #
def test_admin_requires_password_and_exports(client):
    complete_session(client, "baseline", "adminchk")
    complete_session(client, "ai", "adminchk")
    anon = new_client(client)
    assert b"Password" in anon.get("/admin").data
    for name in ("analysis", "sessions", "responses", "participants"):
        assert anon.get(f"/admin/export/{name}.csv").status_code == 302   # redirected, no data
    assert anon.post("/admin", data={"password": "wrong"}).status_code == 401
    anon.post("/admin", data={"password": "admin-pw"})
    r = anon.get("/admin/export/analysis.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    lines = r.get_data(as_text=True).strip().splitlines()
    assert len(lines) == 1 + 24 and "person_key" in lines[0] and "ground_truth" in lines[0]
    sessions = anon.get("/admin/export/sessions.csv").get_data(as_text=True).strip().splitlines()
    assert len(sessions) == 3 and "duration_min" in sessions[0]
    assert b"Session duration" in anon.get("/admin").data
    keys = {line.split(",")[0] for line in sessions[1:]}
    assert len(keys) == 1                                   # both sessions link to one person
    assert b"both</strong> versions: <strong>1" in anon.get("/admin").data
    assert anon.get("/admin/export/nonsense.csv").status_code == 404


# =========================================================================== #
# Click-to-show AI suggestion
# =========================================================================== #
def first_real_trial(client, cond="ai"):
    trial = client.get(f"/api/{cond}/trial/next").get_json()
    if trial["is_attention_check"]:
        client.post(f"/api/{cond}/trial/answer", json={"position": trial["position"], "answer": 0,
                                                       "confidence": 3})
        trial = client.get(f"/api/{cond}/trial/next").get_json()
    return trial


def test_suggestion_only_on_request_and_logged(client, conn):
    start(client)
    pass_instruction_check(client)
    trial = first_real_trial(client)
    assert trial["ai"] is None
    # "Why?" needs the suggestion to be shown first
    r = client.post("/api/ai/explain", json={"position": trial["position"]})
    assert r.status_code == 409 and r.get_json()["error"] == "reveal_first"
    sug = client.post("/api/ai/suggest", json={"position": trial["position"], "reveal_ms": 2300})
    assert sug.status_code == 200 and sug.get_json()["band"] in ("High", "Medium", "Low")
    assert client.post("/api/ai/explain", json={"position": trial["position"]}).status_code == 200
    calls = [r["call_type"] for r in conn.execute(
        "SELECT call_type FROM ai_calls WHERE headline_id=? ORDER BY id", (trial["headline_id"],))]
    assert calls == ["predict", "suggest", "explain"]
    ev = conn.execute("SELECT payload FROM events WHERE type='ai_suggestion_shown'").fetchone()
    assert json.loads(ev["payload"])["reveal_ms"] == 2300
    fb = client.post("/api/ai/trial/answer", json={
        "position": trial["position"], "answer": 1, "confidence": 3,
        "ai_revealed": True, "ai_reveal_ms": 2300}).get_json()
    assert "ai_was_correct" in fb
    row = conn.execute("SELECT * FROM responses ORDER BY id DESC LIMIT 1").fetchone()
    assert row["ai_revealed"] == 1 and row["ai_reveal_ms"] == 2300
    assert row["ai_label_shown"] is not None and row["ai_band_shown"] is not None


def test_not_revealed_is_recorded_as_not_shown(client, conn):
    start(client)
    pass_instruction_check(client)
    trial = first_real_trial(client)
    fb = client.post("/api/ai/trial/answer", json={"position": trial["position"], "answer": 1,
                                                   "confidence": 3}).get_json()
    assert "ai_was_correct" not in fb                         # never saw the AI, so no AI feedback
    row = conn.execute("SELECT * FROM responses ORDER BY id DESC LIMIT 1").fetchone()
    assert row["ai_revealed"] == 0 and row["ai_label_shown"] is None
    assert row["ai_label"] is not None                        # hidden live prediction still logged


def test_suggest_refused_in_baseline_and_out_of_order(client):
    start(client, "baseline")
    pass_instruction_check(client, "baseline")
    trial = first_real_trial(client, "baseline")
    assert client.post("/api/baseline/suggest", json={"position": trial["position"]}).status_code == 403
    c = new_client(client)
    start(c, "ai", "other")
    pass_instruction_check(c)
    trial = first_real_trial(c)
    r = c.post("/api/ai/suggest", json={"position": trial["position"] + 1})
    assert r.status_code == 409
