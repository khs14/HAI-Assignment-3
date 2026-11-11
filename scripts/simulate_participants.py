"""
simulate_participants.py
========================
Runs fake people through BOTH versions (random order) end to end against a RUNNING server (local or
PythonAnywhere), then downloads analysis.csv from /admin and prints a summary.

    python app.py                                   # in one terminal
    python scripts/simulate_participants.py --n 20 --admin-password <pw>

Fake behaviour (so the summary is not trivial):
  * base accuracy ~75% on their own;
  * in the AI version they click "Show AI suggestion" on ~70% of headlines and
    then follow it 80% of the time;
  * 10% are "careless" in their first version: they fail both attention checks
    (-> terminated), then use their one restart and finish properly;
  * on every 4th AI trial they open "Why?".

These are TEST rows. Clear them with  python scripts/reset_db.py --yes
before the real study.
"""
from __future__ import annotations

import argparse
import csv
import http.cookiejar
import io
import json
import random
import statistics
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict


class Client:
    """Tiny JSON client with its own cookie jar (= one browser)."""

    def __init__(self, base):
        self.base = base.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, body=None, form=None):
        data, headers = None, {}
        if body is not None:
            data, headers = json.dumps(body).encode(), {"Content-Type": "application/json"}
        elif form is not None:
            data = urllib.parse.urlencode(form).encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with self.opener.open(req) as r:
                raw = r.read()
                status = r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read(), e.code
        try:
            return status, json.loads(raw)
        except ValueError:
            return status, raw.decode("utf-8", "replace")


def run_session(c, cond, uniq, rng, careless):
    """One person doing one version end to end. Returns the final status."""
    api = f"/api/{cond}"
    status, st = c.call(f"{api}/start", {"consent": True, "uniqname": uniq,
                                         "anonymous": rng.random() < 0.7})
    assert status == 200, st
    fail_checks, restarted = careless, False
    while True:
        stage = st["stage"]
        if stage == "tutorial":
            c.call(f"{api}/event", {"type": "instructions_opened"})
            c.call(f"{api}/attention", {"answer": "relevant" if fail_checks else "not_relevant"})
            _, st = c.call(f"{api}/state")
        elif stage == "trials":
            _, t = c.call(f"{api}/trial/next")
            if t["stage"] != "trials":
                st = t
                continue
            opened, suggestion, reveal_ms = False, None, None
            if t["is_attention_check"]:
                answer = 1 if fail_checks else 0
            else:
                # AI version: click "Show AI suggestion" on ~70% of headlines
                if cond == "ai" and rng.random() < 0.7:
                    reveal_ms = int(rng.uniform(800, 4000))
                    _, suggestion = c.call(f"{api}/suggest", {"position": t["position"],
                                                              "reveal_ms": reveal_ms})
                    if t["position"] % 4 == 0:
                        c.call(f"{api}/explain", {"position": t["position"]})
                        opened = True
                if suggestion and rng.random() < 0.8:
                    answer = 1 if suggestion["label"] == "relevant" else 0
                else:
                    answer = rng.randint(0, 1)
            c.call(f"{api}/trial/answer", {
                "position": t["position"], "answer": answer,
                "confidence": rng.randint(1, 5), "client_time_ms": int(rng.uniform(2500, 14000)),
                "answer_changed_count": int(rng.random() < 0.1), "tab_hidden_during_trial": False,
                "ai_revealed": suggestion is not None, "ai_reveal_ms": reveal_ms,
                "explanation_opened": opened, "explanation_open_count": int(opened),
                "explanation_open_ms": int(rng.uniform(1000, 5000)) if opened else 0})
            _, st = c.call(f"{api}/state")
        elif stage == "questionnaire":
            body = {k: rng.randint(1, 7) for k in
                    ("tlx_mental", "tlx_effort", "tlx_frustration", "tlx_performance")}
            if cond == "ai":
                body.update(ai_trust=rng.randint(2, 7), ai_useful=rng.randint(2, 7),
                            ai_why_helped=rng.choice([None, 3, 5, 6]), comment="simulated")
            _, st = c.call(f"{api}/questionnaire", body)
        elif stage == "terminated":
            if st.get("can_restart") and not restarted:
                restarted, fail_checks = True, False
                _, st = c.call(f"{api}/restart", {})
            else:
                return "terminated"
        elif stage == "completed":
            c.call(f"{api}/feedback", {"feedback": "simulated run"})
            c.call(f"{api}/results")
            return "completed_after_restart" if restarted else "completed"
        else:
            raise RuntimeError(f"unexpected stage {st}")


def run_person(base, rng, careless):
    """A fake classmate: does both versions, in a random order (like the class rotation)."""
    c = Client(base)
    uniq = "s" + "".join(rng.choice(string.ascii_lowercase) for _ in range(6))
    order = rng.sample(["baseline", "ai"], 2)
    return {cond: run_session(c, cond, uniq, rng, careless and i == 0) for i, cond in enumerate(order)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://127.0.0.1:5000")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--admin-password", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--save-csv", default=None, help="also save analysis.csv here")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    outcomes = defaultdict(int)
    for i in range(args.n):
        for cond, result in run_person(args.base_url, rng, careless=(i % 10 == 9)).items():
            outcomes[f"{cond}:{result}"] += 1

    admin = Client(args.base_url)
    admin.call("/admin", form={"password": args.admin_password})
    status, text = admin.call("/admin/export/analysis.csv")
    if status != 200 or not isinstance(text, str):
        sys.exit(f"Could not download analysis.csv (status {status}); wrong admin password?")
    if args.save_csv:
        with open(args.save_csv, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    rows = list(csv.DictReader(io.StringIO(text)))

    print(f"Simulated {args.n} people x 2 versions: " + ", ".join(f"{k} {v}" for k, v in sorted(outcomes.items())))
    print(f"analysis.csv: {len(rows)} trial rows, {len({r['session_id'] for r in rows})} sessions, "
          f"{len({r['person_key'] for r in rows})} people")
    done = [r for r in rows if r["session_status"] == "completed"]
    print(f"\nCompleted sessions only ({len(done)} rows):")
    print(f"{'condition':<10}{'trials':>8}{'accuracy':>10}{'FN rate':>9}{'FP rate':>9}"
          f"{'agree AI':>10}{'mean s':>8}")
    for cond in ("baseline", "ai"):
        rs = [r for r in done if r["condition"] == cond]
        pos = [r for r in rs if r["ground_truth"] == "1"]
        neg = [r for r in rs if r["ground_truth"] == "0"]
        acc = statistics.mean(int(r["correct"]) for r in rs)
        fnr = sum(r["outcome"] == "FN" for r in pos) / len(pos)
        fpr = sum(r["outcome"] == "FP" for r in neg) / len(neg)
        agree = statistics.mean(int(r["agrees_with_ai"]) for r in rs)
        secs = statistics.mean(int(r["client_time_ms"]) for r in rs) / 1000
        print(f"{cond:<10}{len(rs):>8}{acc:>10.3f}{fnr:>9.3f}{fpr:>9.3f}{agree:>10.3f}{secs:>8.1f}")
    ai_rows = [r for r in done if r["condition"] == "ai" and r["ai_revealed"] == "1"]
    all_ai = [r for r in done if r["condition"] == "ai"]
    if all_ai:
        print(f"AI suggestion viewed on {len(ai_rows) / len(all_ai):.3f} of AI-version trials")
    for label, sel in (("AI right", "1"), ("AI wrong", "0")):
        rs = [r for r in ai_rows if r["ai_correct"] == sel]
        if rs:
            print(f"Reliance when {label}: agreed with AI on {statistics.mean(int(r['agrees_with_ai']) for r in rs):.3f}"
                  f" of {len(rs)} trials")
    fallbacks = sum(r["ai_fallback"] == "1" for r in rows)
    print(f"AI fallback rows: {fallbacks}")


if __name__ == "__main__":
    main()
