"""
app.py
======
Flask app for the sustainability-headline study.

There are TWO study versions, each a complete session at its own link:
    /baseline   without AI
    /ai         with a live (synchronous) AI suggestion, shown when the participant
                clicks "Show AI suggestion", and a "Why?" explanation

Flow inside one session (the server decides the stage, so a refresh resumes):
    consent -> tutorial + instruction check -> intro -> 12 headlines (+1 attention
    check) -> questionnaire -> completed (review, results board, optional delete)

The same uniqname can do each version once (plus one restart after termination).
Its two sessions are linked by a keyed hash of the uniqname, so the analysis can
pair a person's "without AI" and "with AI" results, and a person never sees the
same headline in both versions.

KEY RULE: ground truth never reaches the browser before the participant's answer
for that headline is saved. Correctness is computed here. The correct label is
revealed only in the response to the answer (per-trial feedback) and on the
review page after completion.

Run locally:   python app.py   (then open http://127.0.0.1:5000)
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import random
import re
import secrets
import sqlite3
from functools import wraps

from flask import (Flask, Response, abort, g, jsonify, redirect, render_template,
                   request, session, url_for)

import config
import db
import sampling
from ai_service import AIService

TLX_ITEMS = ["tlx_mental", "tlx_effort", "tlx_frustration", "tlx_performance"]
AI_ITEMS = ["ai_trust", "ai_useful"]       # required in the AI version
EVENT_TYPES = {
    "instructions_opened", "definitions_opened", "definitions_closed", "tab_hidden",
    "tab_visible", "explanation_opened", "explanation_closed", "intro_shown",
    "review_opened", "results_opened", "ai_suggestion_shown",
}
EXPORT_TABLES = ["participants", "trials", "assignments", "responses", "ai_calls",
                 "events", "questionnaire"]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(message or code)
        self.status, self.code, self.message = status, code, message or code


# =========================================================================== #
# App factory
# =========================================================================== #
def create_app(overrides: dict | None = None) -> Flask:
    overrides = overrides or {}
    app = Flask(__name__)
    secrets_ = {k: overrides[k] for k in config.SECRET_NAMES if k in overrides}
    if len(secrets_) < len(config.SECRET_NAMES):
        secrets_ = {**config.load_secrets(), **secrets_}
    app.config.update(
        SECRET_KEY=secrets_["SECRET_KEY"],
        ADMIN_PASSWORD=secrets_["ADMIN_PASSWORD"],
        UNIQNAME_KEY=secrets_["UNIQNAME_KEY"],
        DB_PATH=str(overrides.get("DB_PATH", config.DB_PATH)),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_HTTPONLY=True,
        TESTING=overrides.get("TESTING", False),
    )
    config.check_band_config()
    db.init_db(app.config["DB_PATH"])

    # The model is loaded ONCE here, not per request.
    app.extensions["ai"] = overrides.get("AI_SERVICE") or AIService()
    app.extensions["rng"] = overrides.get("RNG") or random.SystemRandom()
    tutorial = json.loads(config.TUTORIAL_FILE.read_text(encoding="utf-8"))

    # ---- per-request database connection ----------------------------------
    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = db.connect(app.config["DB_PATH"])
        return g.db

    @app.teardown_appcontext
    def close_db(_exc):
        conn = g.pop("db", None)
        if conn is not None:
            conn.close()

    @app.errorhandler(ApiError)
    def handle_api_error(err: ApiError):
        return jsonify({"ok": False, "error": err.code, "message": err.message}), err.status

    @app.errorhandler(500)
    def handle_500(_err):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "server_error",
                            "message": "Something went wrong on the server."}), 500
        return "Server error", 500

    # ---- helpers ----------------------------------------------------------
    def ai() -> AIService:
        return app.extensions["ai"]

    def check_condition(cond: str) -> str:
        if cond not in config.CONDITIONS:
            abort(404)
        return cond

    def body() -> dict:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            raise ApiError(400, "bad_request", "Expected a JSON object.")
        return data

    def hash_uniqname(uniqname: str) -> str:
        key = app.config["UNIQNAME_KEY"].encode()
        return hmac.new(key, uniqname.encode(), hashlib.sha256).hexdigest()

    def current_participant(cond: str, required=True):
        """The session for this version (each version has its own cookie entry)."""
        pid = session.get(f"pid_{cond}")
        row = None
        if pid:
            row = get_db().execute("SELECT * FROM participants WHERE id=? AND condition=?",
                                   (pid, cond)).fetchone()
        if row is None and required:
            raise ApiError(401, "no_session", "Please start the study from the welcome page.")
        return row

    def log_event(pid, type_, payload=None, client_time=None):
        get_db().execute(
            "INSERT INTO events (participant_id, type, payload, client_time, server_time) "
            "VALUES (?, ?, ?, ?, ?)",
            (pid, type_, json.dumps(payload) if payload is not None else None,
             client_time, db.now_iso()))

    def log_ai_call(pid, headline_id, call_type, condition, result, explanation=None):
        get_db().execute("""
            INSERT INTO ai_calls (participant_id, headline_id, call_type, condition, model_name,
                threshold, raw_score, label, band, inference_ms, fallback, cached, error,
                explanation_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, headline_id, call_type, condition, ai().model_name, ai().threshold,
             result.get("score"), result.get("label"), result.get("band"),
             result.get("inference_ms"), int(bool(result.get("fallback"))),
             int(bool(result.get("cached"))), result.get("error"),
             json.dumps(explanation) if explanation is not None else None, db.now_iso()))

    def attempts_for(uhash: str, cond: str):
        return get_db().execute(
            "SELECT * FROM participants WHERE uniqname_hash=? AND condition=? ORDER BY attempt",
            (uhash, cond)).fetchall()

    def create_participant(cond, uhash, uniqname, anonymous, attempt):
        conn = get_db()
        other = [c for c in config.CONDITIONS if c != cond][0]
        did_other_first = conn.execute(
            "SELECT 1 FROM participants WHERE uniqname_hash=? AND condition=? LIMIT 1",
            (uhash, other)).fetchone() is not None
        pid = secrets.token_urlsafe(16)
        conn.execute("""
            INSERT INTO participants (id, uniqname_hash, uniqname, anonymous, condition, attempt,
                version_order, status, consent_at, user_agent)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)""",
            (pid, uhash, None if anonymous else uniqname, int(anonymous), cond, attempt,
             2 if did_other_first else 1, db.now_iso(),
             (request.headers.get("User-Agent") or "")[:300]))
        sampling.create_assignments(conn, pid, cond, uhash, app.extensions["rng"])
        conn.commit()
        session[f"pid_{cond}"] = pid
        return pid

    def can_restart(p) -> bool:
        if p is None or p["status"] != "terminated":
            return False
        return len(attempts_for(p["uniqname_hash"], p["condition"])) < config.MAX_ATTEMPTS_PER_VERSION

    def next_assignment(pid):
        """Lowest-position assignment with no response yet."""
        return get_db().execute("""
            SELECT a.* FROM assignments a
            LEFT JOIN responses r ON r.participant_id = a.participant_id AND r.position = a.position
            WHERE a.participant_id = ? AND r.id IS NULL
            ORDER BY a.position LIMIT 1""", (pid,)).fetchone()

    def compute_stage(p) -> dict:
        """Where is this participant in the session? (the server is the source of truth)"""
        if p is None:
            return {"stage": "consent"}
        if p["status"] == "withdrawn":
            return {"stage": "withdrawn"}
        if p["status"] == "completed":
            return {"stage": "completed", "completion_code": p["completion_code"]}
        if p["status"] == "terminated":
            return {"stage": "terminated", "can_restart": can_restart(p)}
        if p["instruction_check_correct"] is None:
            return {"stage": "tutorial"}
        conn = get_db()
        total = conn.execute("SELECT COUNT(*) FROM assignments WHERE participant_id=?",
                             (p["id"],)).fetchone()[0]
        answered = conn.execute("SELECT COUNT(*) FROM responses WHERE participant_id=?",
                                (p["id"],)).fetchone()[0]
        if answered < total:
            return {"stage": "trials", "answered": answered}
        return {"stage": "questionnaire"}

    def require_stage(p, stage):
        st = compute_stage(p)
        if st["stage"] != stage:
            raise ApiError(409, "wrong_stage", f"Expected stage {stage}, participant is at {st['stage']}.")
        return st

    def ai_payload(result) -> dict:
        """What the browser is allowed to see about the AI's output."""
        out = {"label": config.LABEL_NAMES[result["label"]],
               "label_text": config.LABEL_TEXT[result["label"]],
               "band": result["band"]}
        if config.SHOW_SCORE:
            out["score_pct"] = round(result["score"] * 100)
        return out

    def terminate_if_needed(pid):
        conn = get_db()
        p = conn.execute("SELECT * FROM participants WHERE id=?", (pid,)).fetchone()
        if p["attention_failures"] >= config.MAX_ATTENTION_FAILURES and p["status"] == "active":
            conn.execute("UPDATE participants SET status='terminated', terminated_at=? WHERE id=?",
                         (db.now_iso(), pid))
            return True
        return False

    # =======================================================================
    # Pages
    # =======================================================================
    @app.get("/")
    def landing():
        return render_template("landing.html", n_trials=config.TRIALS_PER_SESSION)

    @app.get("/<cond>")
    def study_page(cond):
        check_condition(cond)
        return render_template("index.html", tutorial=tutorial["examples"], condition=cond,
                               condition_text=config.CONDITION_TEXT[cond],
                               n_trials=config.TRIALS_PER_SESSION)

    # =======================================================================
    # Participant API (one set per version: /api/baseline/... and /api/ai/...)
    # =======================================================================
    @app.get("/api/<cond>/state")
    def api_state(cond):
        check_condition(cond)
        return jsonify({"ok": True, **compute_stage(current_participant(cond, required=False))})

    @app.post("/api/<cond>/start")
    def api_start(cond):
        """Consent accepted -> create the session and sample its headlines (or resume)."""
        check_condition(cond)
        data = body()
        if data.get("consent") is not True:
            raise ApiError(400, "consent_required", "Please agree to take part to continue.")
        uniqname = str(data.get("uniqname", "")).strip().lower()
        if not re.fullmatch(config.UNIQNAME_PATTERN, uniqname):
            raise ApiError(400, "bad_uniqname", "Please enter your uniqname (3-8 letters, "
                                                "the part of your UMich email before @).")
        anonymous = bool(data.get("anonymous"))
        uhash = hash_uniqname(uniqname)
        attempts = attempts_for(uhash, cond)

        if any(a["status"] == "terminated" for a in attempts):
            raise ApiError(409, "terminated",
                           "Your earlier session of this version ended after two missed attention "
                           "checks, so it can't be taken again. Thank you for your time.")
        if any(a["status"] in ("completed", "withdrawn") for a in attempts):
            raise ApiError(409, "already_done",
                           f"This uniqname has already done the {config.CONDITION_TEXT[cond]} "
                           "version. Thank you!")
        active = [a for a in attempts if a["status"] == "active"]
        if active:  # resume (e.g. a different device or cleared cookies)
            session[f"pid_{cond}"] = active[0]["id"]
            return jsonify({"ok": True, "resumed": True, **compute_stage(active[0])})
        if len(attempts) >= config.MAX_ATTEMPTS_PER_VERSION:
            raise ApiError(409, "no_attempts_left", "This uniqname has no attempts left for this "
                                                    "version. Thank you for your time.")
        create_participant(cond, uhash, uniqname, anonymous, len(attempts) + 1)
        return jsonify({"ok": True, "resumed": False, **compute_stage(current_participant(cond))})

    @app.post("/api/<cond>/restart")
    def api_restart(cond):
        """After termination: one fresh attempt with a NEW session id and new headlines."""
        check_condition(cond)
        p = current_participant(cond)
        if not can_restart(p):
            raise ApiError(409, "cannot_restart", "A restart is not available.")
        create_participant(cond, p["uniqname_hash"], p["uniqname"], bool(p["anonymous"]),
                           p["attempt"] + 1)
        return jsonify({"ok": True, **compute_stage(current_participant(cond))})

    @app.post("/api/<cond>/attention")
    def api_attention(cond):
        """Instruction check after the tutorial (multiple choice). Scored here."""
        check_condition(cond)
        p = current_participant(cond)
        require_stage(p, "tutorial")
        answer = str(body().get("answer", ""))
        if answer not in ("relevant", "not_relevant"):
            raise ApiError(400, "bad_answer")
        correct = answer == config.INSTRUCTION_CHECK_CORRECT
        conn = get_db()
        # instruction_check_answer keeps every attempt, e.g. "relevant,not_relevant"
        conn.execute("UPDATE participants SET instruction_check_answer = "
                     "COALESCE(instruction_check_answer || ',', '') || ? WHERE id=?", (answer, p["id"]))
        terminated = False
        if correct:
            conn.execute("UPDATE participants SET instruction_check_correct=1 WHERE id=?", (p["id"],))
        else:
            # first miss: stay on the check (stage stays 'tutorial') and try again
            conn.execute("UPDATE participants SET attention_failures = attention_failures + 1 "
                         "WHERE id=?", (p["id"],))
            log_event(p["id"], "attention_check_failed", {"check": "instruction", "answer": answer})
            terminated = terminate_if_needed(p["id"])
            if terminated:
                conn.execute("UPDATE participants SET instruction_check_correct=0 WHERE id=?", (p["id"],))
        conn.commit()
        return jsonify({"ok": True, "correct": correct, "terminated": terminated,
                        "retry": not correct and not terminated,
                        "rule": "Words like \"climate\" or \"heat\" used in a non-environmental "
                                "sense (political climate, Miami Heat) do not make a headline "
                                "relevant. Judge the headline's main subject."})

    @app.get("/api/<cond>/trial/next")
    def api_trial_next(cond):
        check_condition(cond)
        p = current_participant(cond)
        st = compute_stage(p)
        if st["stage"] != "trials":
            return jsonify({"ok": True, **st})
        a = next_assignment(p["id"])
        conn = get_db()
        real_before = conn.execute(
            "SELECT COUNT(*) FROM assignments WHERE participant_id=? AND position<? "
            "AND is_attention_check=0", (p["id"], a["position"])).fetchone()[0]
        out = {"ok": True, "stage": "trials", "condition": cond, "position": a["position"],
               "index": real_before + 1, "total": config.TRIALS_PER_SESSION,
               "is_attention_check": bool(a["is_attention_check"])}

        if a["is_attention_check"]:
            out.update(headline_id=config.ATTENTION_HEADLINE_ID, title=config.ATTENTION_TEXT, ai=None)
            conn.execute("UPDATE assignments SET first_served_at=COALESCE(first_served_at, ?) "
                         "WHERE id=?", (db.now_iso(), a["id"]))
        else:
            t = conn.execute("SELECT headline_id, title, ai_score FROM trials WHERE headline_id=?",
                             (a["headline_id"],)).fetchone()
            # Live prediction on EVERY real trial, logged but NOT sent to the browser.
            # (In the AI version the participant gets the output via /suggest on request.)
            result = ai().predict(t["title"], fallback_score=t["ai_score"])
            log_ai_call(p["id"], t["headline_id"], "predict", cond, result)
            conn.execute("""UPDATE assignments SET ai_score_raw=?, ai_label=?, ai_band=?, ai_fallback=?,
                            first_served_at=COALESCE(first_served_at, ?) WHERE id=?""",
                         (result["score"], result["label"], result["band"], int(result["fallback"]),
                          db.now_iso(), a["id"]))
            out.update(headline_id=t["headline_id"], title=t["title"], ai=None)
        conn.commit()
        return jsonify(out)

    @app.post("/api/<cond>/trial/answer")
    def api_trial_answer(cond):
        """Validate it is the expected trial, reject duplicates, score here, store, give feedback."""
        check_condition(cond)
        p = current_participant(cond)
        data = body()
        conn = get_db()
        try:
            position = int(data["position"])
            answer = int(data["answer"])
            confidence = int(data["confidence"])
        except (KeyError, TypeError, ValueError):
            raise ApiError(400, "bad_request", "position, answer and confidence are required.")
        if answer not in (0, 1) or not 1 <= confidence <= 5:
            raise ApiError(400, "bad_request", "answer must be 0/1 and confidence 1-5.")

        if conn.execute("SELECT 1 FROM responses WHERE participant_id=? AND position=?",
                        (p["id"], position)).fetchone():
            raise ApiError(409, "duplicate", "This headline was already answered.")
        require_stage(p, "trials")
        a = next_assignment(p["id"])
        if a is None or a["position"] != position:
            raise ApiError(409, "out_of_order", "That is not the current headline.")
        if a["first_served_at"] is None:
            raise ApiError(409, "not_served", "This headline has not been shown yet.")

        is_attn = bool(a["is_attention_check"])
        if is_attn:
            gt = 0  # the attention check always asks for "Not relevant"
        else:
            gt = conn.execute("SELECT ground_truth FROM trials WHERE headline_id=?",
                              (a["headline_id"],)).fetchone()["ground_truth"]
        correct = int(answer == gt)
        shown = cond == "ai" and not is_attn and a["ai_revealed_at"] is not None
        ai_correct = None if is_attn else int(a["ai_label"] == gt)

        def as_int(key, lo=0, hi=10**9):
            try:
                return max(lo, min(hi, int(data.get(key))))
            except (TypeError, ValueError):
                return None

        terminated = False
        if is_attn and not correct:
            conn.execute("UPDATE participants SET attention_failures = attention_failures + 1 "
                         "WHERE id=?", (p["id"],))
            log_event(p["id"], "attention_check_failed",
                      {"check": "in_task", "position": position,
                       "client_time_ms": as_int("client_time_ms", 0, 3_600_000)})
            terminated = terminate_if_needed(p["id"])
            if not terminated:
                # first miss: nothing is stored as an answer, so /trial/next serves this check again
                conn.commit()
                return jsonify({"ok": True, "is_attention_check": True, "your_answer_correct": False,
                                "correct_label": "not_relevant", "correct_label_text": "Not relevant",
                                "retry": True, "terminated": False})

        try:
            conn.execute("""
                INSERT INTO responses (participant_id, assignment_id, headline_id, condition,
                    position, is_attention_check, answer, correct, confidence, client_time_ms,
                    served_at, server_received_at, answer_changed_count, tab_hidden_during_trial,
                    ai_revealed, ai_reveal_ms,
                    ai_label_shown, ai_band_shown, ai_score_raw, ai_label, ai_correct, ai_fallback,
                    explanation_opened, explanation_open_count, explanation_open_ms)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (p["id"], a["id"], a["headline_id"], cond, position, int(is_attn),
                 answer, correct, confidence, as_int("client_time_ms", 0, 3_600_000),
                 a["first_served_at"], db.now_iso(), as_int("answer_changed_count", 0, 1000),
                 int(bool(data.get("tab_hidden_during_trial"))),
                 (int(shown) if cond == "ai" and not is_attn else None),
                 (as_int("ai_reveal_ms", 0, 3_600_000) if shown else None),
                 a["ai_label"] if shown else None, a["ai_band"] if shown else None,
                 a["ai_score_raw"], a["ai_label"], ai_correct, a["ai_fallback"],
                 int(bool(data.get("explanation_opened"))) if shown else None,
                 as_int("explanation_open_count", 0, 1000) if shown else None,
                 as_int("explanation_open_ms", 0, 3_600_000) if shown else None))
        except sqlite3.IntegrityError:
            raise ApiError(409, "duplicate", "This headline was already answered.")

        conn.commit()

        # Feedback: the correct label is revealed only now, after the answer is locked in.
        feedback = {"ok": True, "is_attention_check": is_attn, "your_answer_correct": bool(correct),
                    "correct_label": config.LABEL_NAMES[gt], "correct_label_text": config.LABEL_TEXT[gt],
                    "terminated": terminated}
        if shown:
            feedback["ai_was_correct"] = bool(ai_correct)
        return jsonify(feedback)

    @app.post("/api/<cond>/suggest")
    def api_suggest(cond):
        """
        "Show AI suggestion" button (AI version). This is the synchronous AI call:
        the model runs live at the moment the participant asks, the output is
        logged (ai_calls, call_type='suggest'), and only label + band are returned.
        """
        check_condition(cond)
        if cond != "ai":
            raise ApiError(403, "not_available", "AI suggestions are only available in the AI version.")
        p = current_participant(cond)
        require_stage(p, "trials")
        a = next_assignment(p["id"])
        data = body()
        try:
            requested = int(data["position"])
        except (KeyError, TypeError, ValueError):
            raise ApiError(400, "bad_request")
        if a["position"] != requested:
            raise ApiError(409, "out_of_order", "That is not the current headline.")
        if a["is_attention_check"]:
            raise ApiError(403, "not_available", "No AI suggestion for the attention check.")
        if a["first_served_at"] is None:
            raise ApiError(409, "not_served", "This headline has not been shown yet.")
        conn = get_db()
        t = conn.execute("SELECT headline_id, title, ai_score FROM trials WHERE headline_id=?",
                         (a["headline_id"],)).fetchone()
        result = ai().predict(t["title"], fallback_score=t["ai_score"])
        log_ai_call(p["id"], t["headline_id"], "suggest", cond, result)
        conn.execute("""UPDATE assignments SET ai_score_raw=?, ai_label=?, ai_band=?, ai_fallback=?,
                        ai_revealed_at=COALESCE(ai_revealed_at, ?) WHERE id=?""",
                     (result["score"], result["label"], result["band"], int(result["fallback"]),
                      db.now_iso(), a["id"]))
        log_event(p["id"], "ai_suggestion_shown",
                  {"position": a["position"], "headline_id": t["headline_id"],
                   "reveal_ms": data.get("reveal_ms")})
        conn.commit()
        return jsonify({"ok": True, **ai_payload(result)})

    @app.post("/api/<cond>/explain")
    def api_explain(cond):
        """'Why?' button: leave-one-word-out word importances for the CURRENT headline (AI version)."""
        check_condition(cond)
        if cond != "ai":
            raise ApiError(403, "not_available", "Explanations are only available in the AI version.")
        p = current_participant(cond)
        require_stage(p, "trials")
        a = next_assignment(p["id"])
        try:
            requested = int(body()["position"])
        except (KeyError, TypeError, ValueError):
            raise ApiError(400, "bad_request")
        if a["position"] != requested:
            raise ApiError(409, "out_of_order", "That is not the current headline.")
        if a["is_attention_check"]:
            raise ApiError(403, "not_available", "No explanation for the attention check.")
        if a["ai_revealed_at"] is None:
            raise ApiError(409, "reveal_first", "Show the AI suggestion first.")
        t = get_db().execute("SELECT headline_id, title FROM trials WHERE headline_id=?",
                             (a["headline_id"],)).fetchone()
        result = ai().explain(t["headline_id"], t["title"])
        explanation = {k: result[k] for k in ("toward_relevant", "toward_not_relevant")}
        log_ai_call(p["id"], t["headline_id"], "explain", cond, result, explanation)
        get_db().commit()
        strip = lambda items: [{"index": w["index"], "word": w["word"]} for w in items]  # noqa: E731
        return jsonify({"ok": True, "available": result["error"] is None,
                        "toward_relevant": strip(result["toward_relevant"]),
                        "toward_not_relevant": strip(result["toward_not_relevant"])})

    @app.post("/api/<cond>/questionnaire")
    def api_questionnaire(cond):
        check_condition(cond)
        p = current_participant(cond)
        require_stage(p, "questionnaire")
        data = body()
        values = {}
        for item in TLX_ITEMS + (AI_ITEMS if cond == "ai" else []):
            try:
                v = int(data[item])
            except (KeyError, TypeError, ValueError):
                raise ApiError(400, "missing_item", f"Please answer every question ({item}).")
            if not 1 <= v <= 7:
                raise ApiError(400, "bad_value", f"{item} must be 1-7.")
            values[item] = v
        if cond == "ai":
            try:
                v = int(data.get("ai_why_helped"))
                values["ai_why_helped"] = v if 1 <= v <= 7 else None
            except (TypeError, ValueError):
                values["ai_why_helped"] = None   # "I didn't use them"
        comment = str(data.get("comment") or "")[:2000] or None
        conn = get_db()
        conn.execute("""
            INSERT INTO questionnaire (participant_id, condition, tlx_mental, tlx_effort,
                tlx_frustration, tlx_performance, ai_trust, ai_useful, ai_why_helped, comment,
                submitted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (p["id"], cond, values["tlx_mental"], values["tlx_effort"], values["tlx_frustration"],
             values["tlx_performance"], values.get("ai_trust"), values.get("ai_useful"),
             values.get("ai_why_helped"), comment, db.now_iso()))
        code = ("NOAI-" if cond == "baseline" else "AI-") + secrets.token_hex(3).upper()
        conn.execute("UPDATE participants SET status='completed', completed_at=?, completion_code=? "
                     "WHERE id=?", (db.now_iso(), code, p["id"]))
        conn.commit()
        return jsonify({"ok": True, **compute_stage(current_participant(cond))})

    @app.post("/api/<cond>/event")
    def api_event(cond):
        check_condition(cond)
        p = current_participant(cond, required=False)
        data = body()
        type_ = str(data.get("type", ""))
        if type_ not in EVENT_TYPES:
            raise ApiError(400, "bad_event_type")
        payload = data.get("payload")
        if payload is not None and len(json.dumps(payload)) > 4000:
            raise ApiError(400, "payload_too_large")
        log_event(p["id"] if p else None, type_, payload, str(data.get("client_time", ""))[:40] or None)
        get_db().commit()
        return jsonify({"ok": True})

    # ---- after completion: feedback, review, results board, delete ----------
    def require_completed(cond):
        p = current_participant(cond)
        if p["status"] != "completed":
            raise ApiError(409, "not_completed", "Available after you finish the study.")
        return p

    @app.post("/api/<cond>/feedback")
    def api_feedback(cond):
        check_condition(cond)
        p = require_completed(cond)
        text = str(body().get("feedback") or "")[:3000]
        get_db().execute("UPDATE participants SET final_feedback=? WHERE id=?", (text, p["id"]))
        get_db().commit()
        return jsonify({"ok": True})

    @app.get("/api/<cond>/review")
    def api_review(cond):
        """Everything this participant submitted in this version (only after completion)."""
        check_condition(cond)
        p = require_completed(cond)
        rows = get_db().execute("""
            SELECT r.position, r.is_attention_check, r.answer, r.correct, r.confidence,
                   r.client_time_ms, r.ai_label_shown, r.ai_band_shown, r.explanation_opened,
                   t.title, t.ground_truth
            FROM responses r LEFT JOIN trials t ON t.headline_id = r.headline_id
            WHERE r.participant_id=? ORDER BY r.position""", (p["id"],)).fetchall()
        items = []
        for r in rows:
            gt = 0 if r["is_attention_check"] else r["ground_truth"]
            items.append({
                "is_attention_check": bool(r["is_attention_check"]),
                "title": config.ATTENTION_TEXT if r["is_attention_check"] else r["title"],
                "your_answer": config.LABEL_TEXT[r["answer"]], "correct": bool(r["correct"]),
                "correct_label": config.LABEL_TEXT[gt], "confidence": r["confidence"],
                "seconds": round(r["client_time_ms"] / 1000, 1) if r["client_time_ms"] else None,
                "ai_suggestion": (f"{config.LABEL_TEXT[r['ai_label_shown']]} ({r['ai_band_shown']})"
                                  if r["ai_label_shown"] is not None
                                  else ("Not viewed" if cond == "ai" and not r["is_attention_check"]
                                        else None)),
                "used_why": bool(r["explanation_opened"]) if cond == "ai" else None,
            })
        q = get_db().execute(
            "SELECT tlx_mental, tlx_effort, tlx_frustration, tlx_performance, ai_trust, ai_useful, "
            "ai_why_helped, comment FROM questionnaire WHERE participant_id=?", (p["id"],)).fetchone()
        return jsonify({"ok": True, "completion_code": p["completion_code"], "responses": items,
                        "questionnaire": dict(q) if q else None})

    @app.get("/api/<cond>/results")
    def api_results(cond):
        """Anonymous results board: your accuracy vs. everyone, in both versions."""
        check_condition(cond)
        p = require_completed(cond)
        conn = get_db()

        def stats(where, params):
            r = conn.execute(f"""SELECT COUNT(*) n, AVG(r.correct) acc, AVG(r.client_time_ms) ms,
                COUNT(DISTINCT r.participant_id) people
                FROM responses r JOIN participants p ON p.id = r.participant_id
                WHERE r.is_attention_check = 0 AND p.status = 'completed' AND {where}""",
                params).fetchone()
            return {"n": r["n"], "people": r["people"],
                    "accuracy": round(r["acc"], 3) if r["acc"] is not None else None,
                    "mean_seconds": round(r["ms"] / 1000, 1) if r["ms"] is not None else None}

        out = {"ok": True, "condition": cond,
               "you": stats("r.participant_id=?", (p["id"],)),
               "baseline": stats("r.condition='baseline'", ()),
               "ai": stats("r.condition='ai'", ())}
        ai_acc = conn.execute("""SELECT AVG(r.ai_correct) FROM responses r JOIN participants p
            ON p.id=r.participant_id WHERE r.is_attention_check=0 AND r.condition='ai'
            AND p.status='completed'""").fetchone()[0]
        out["ai_alone_accuracy"] = round(ai_acc, 3) if ai_acc is not None else None
        per = [r[0] for r in conn.execute("""SELECT AVG(r.correct) FROM responses r
            JOIN participants p ON p.id=r.participant_id WHERE r.is_attention_check=0
            AND p.status='completed' AND p.condition=? GROUP BY r.participant_id""", (cond,))]
        mine = out["you"]["accuracy"]
        out["share_you_beat_or_tied"] = (round(sum(x <= mine + 1e-9 for x in per) / len(per), 3)
                                         if per and mine is not None else None)
        return jsonify(out)

    @app.post("/api/<cond>/withdraw")
    def api_withdraw(cond):
        """Delete my submission for this version. Keeps only a 'withdrawn' marker."""
        check_condition(cond)
        p = current_participant(cond)
        if body().get("confirm") is not True:
            raise ApiError(400, "confirm_required")
        conn = get_db()
        for table in db.PARTICIPANT_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE participant_id=?", (p["id"],))
        conn.execute("""UPDATE participants SET status='withdrawn', withdrawn_at=?, uniqname=NULL,
                        user_agent=NULL, completion_code=NULL, final_feedback=NULL,
                        instruction_check_answer=NULL WHERE id=?""", (db.now_iso(), p["id"]))
        conn.commit()
        session.pop(f"pid_{cond}", None)
        return jsonify({"ok": True, "stage": "withdrawn"})

    # =======================================================================
    # Admin
    # =======================================================================
    def admin_required(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not session.get("is_admin"):
                return redirect(url_for("admin"))
            return view(*args, **kwargs)
        return wrapper

    @app.route("/admin", methods=["GET", "POST"])
    def admin():
        error = None
        if request.method == "POST":
            given = request.form.get("password", "")
            if hmac.compare_digest(given.encode(), app.config["ADMIN_PASSWORD"].encode()):
                session["is_admin"] = True
                return redirect(url_for("admin"))
            error = "Wrong password."
        if not session.get("is_admin"):
            return render_template("admin_login.html", error=error), (401 if error else 200)
        conn = get_db()
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in EXPORT_TABLES}
        status = {}
        for r in conn.execute("SELECT condition, status, COUNT(*) n FROM participants "
                              "GROUP BY condition, status"):
            status.setdefault(r["condition"], {})[r["status"]] = r["n"]
        both = conn.execute("""SELECT COUNT(*) FROM (SELECT uniqname_hash FROM participants
            WHERE status='completed' GROUP BY uniqname_hash HAVING COUNT(DISTINCT condition)=2)""").fetchone()[0]
        fallbacks = conn.execute("SELECT COUNT(*) FROM ai_calls WHERE fallback=1").fetchone()[0]
        # session duration (consent -> completion), per version, completed sessions only
        durations = {}
        for cond in config.CONDITIONS:
            mins = sorted(r[0] for r in conn.execute(
                "SELECT (julianday(completed_at) - julianday(consent_at)) * 1440 FROM participants "
                "WHERE status='completed' AND condition=?", (cond,)))
            if mins:
                durations[cond] = {
                    "n": len(mins), "median": round(mins[len(mins) // 2], 1),
                    "p90": round(mins[min(len(mins) - 1, int(0.9 * len(mins)))], 1),
                    "max": round(mins[-1], 1),
                    "over": sum(m > config.SESSION_TIME_LIMIT_MIN for m in mins)}
        lookup = None
        if request.args.get("uniqname"):
            u = request.args["uniqname"].strip().lower()
            rows = conn.execute("SELECT * FROM participants WHERE uniqname_hash=? "
                                "ORDER BY consent_at", (hash_uniqname(u),)).fetchall()
            lookup = {"uniqname": u, "attempts": [dict(condition=r["condition"], status=r["status"],
                      attempt=r["attempt"], completed_at=r["completed_at"], code=r["completion_code"])
                      for r in rows]}
        return render_template("admin.html", counts=counts, status=status, both=both,
                               durations=durations, limit=config.SESSION_TIME_LIMIT_MIN,
                               fallbacks=fallbacks, tables=EXPORT_TABLES, lookup=lookup,
                               model_error=ai().load_error, condition_text=config.CONDITION_TEXT)

    @app.post("/admin/logout")
    def admin_logout():
        session.pop("is_admin", None)
        return redirect(url_for("admin"))

    def csv_response(cursor, filename):
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow([d[0] for d in cursor.description])
        w.writerows(cursor.fetchall())
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={filename}"})

    @app.get("/admin/export/<name>.csv")
    @admin_required
    def admin_export(name):
        conn = get_db()
        if name == "analysis":
            return csv_response(conn.execute(ANALYSIS_SQL), "analysis.csv")
        if name == "sessions":
            return csv_response(conn.execute(SESSIONS_SQL), "sessions.csv")
        if name not in EXPORT_TABLES:
            abort(404)
        return csv_response(conn.execute(f"SELECT * FROM {name}"), f"{name}.csv")

    return app


# One row per real (non-attention) trial response, joined with everything needed for analysis.
# person_key links the same person's two versions (a short prefix of the keyed uniqname hash).
ANALYSIS_SQL = """
SELECT
    substr(p.uniqname_hash, 1, 12) AS person_key, r.participant_id AS session_id,
    p.condition, p.version_order, p.attempt, p.status AS session_status,
    p.attention_failures, p.instruction_check_correct,
    r.position, r.headline_id, t.title, t.tier, t.region, t.keyword,
    t.ground_truth, r.answer, r.correct, r.confidence,
    CASE WHEN t.ground_truth = 1 AND r.answer = 0 THEN 'FN'
         WHEN t.ground_truth = 0 AND r.answer = 1 THEN 'FP'
         WHEN t.ground_truth = 1 THEN 'TP' ELSE 'TN' END AS outcome,
    r.client_time_ms, r.tab_hidden_during_trial, r.answer_changed_count,
    r.ai_score_raw, r.ai_label, r.ai_correct, r.ai_fallback,
    t.ai_score AS ai_score_precomputed,
    r.ai_revealed, r.ai_reveal_ms, r.ai_label_shown, r.ai_band_shown,
    CASE WHEN r.answer = r.ai_label THEN 1 ELSE 0 END AS agrees_with_ai,
    r.explanation_opened, r.explanation_open_count, r.explanation_open_ms,
    r.served_at, r.server_received_at
FROM responses r
JOIN participants p ON p.id = r.participant_id
LEFT JOIN trials t ON t.headline_id = r.headline_id
WHERE r.is_attention_check = 0
ORDER BY p.consent_at, r.position
"""

# One row per session: accuracy, time and questionnaire, ready for paired tests.
SESSIONS_SQL = """
SELECT
    substr(p.uniqname_hash, 1, 12) AS person_key, p.id AS session_id, p.condition,
    p.version_order, p.attempt, p.status, p.attention_failures, p.consent_at, p.completed_at,
    ROUND((julianday(p.completed_at) - julianday(p.consent_at)) * 1440, 2) AS duration_min,
    COUNT(r.id) AS n_trials, AVG(r.correct) AS accuracy,
    AVG(CASE WHEN t.ground_truth = 1 THEN 1.0 - r.correct END) AS fn_rate,
    AVG(CASE WHEN t.ground_truth = 0 THEN 1.0 - r.correct END) AS fp_rate,
    AVG(r.client_time_ms) / 1000.0 AS mean_seconds, AVG(r.confidence) AS mean_confidence,
    AVG(r.ai_revealed) AS share_ai_viewed,
    -- reliance: in the AI version only trials where the suggestion was viewed;
    -- in the baseline version, agreement with the hidden AI (reference point)
    AVG(CASE WHEN r.ai_correct = 1 AND (r.condition = 'baseline' OR r.ai_revealed = 1)
             THEN r.answer = r.ai_label END) AS agree_ai_when_right,
    AVG(CASE WHEN r.ai_correct = 0 AND (r.condition = 'baseline' OR r.ai_revealed = 1)
             THEN r.answer = r.ai_label END) AS agree_ai_when_wrong,
    AVG(r.explanation_opened) AS share_why_opened,
    q.tlx_mental, q.tlx_effort, q.tlx_frustration, q.tlx_performance,
    q.ai_trust, q.ai_useful, q.ai_why_helped, q.comment, p.final_feedback
FROM participants p
LEFT JOIN responses r ON r.participant_id = p.id AND r.is_attention_check = 0
LEFT JOIN trials t ON t.headline_id = r.headline_id
LEFT JOIN questionnaire q ON q.participant_id = p.id
WHERE p.status != 'withdrawn'
GROUP BY p.id
ORDER BY p.consent_at
"""


if __name__ == "__main__":
    create_app().run(debug=False, port=5000)
