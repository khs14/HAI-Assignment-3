# Human-AI study: sustainability headline relevance

A Flask web app for a CSE 594 (Human-AI Interaction) human-subjects study.
Participants read English news headlines and decide whether each one is
**relevant to environmental sustainability** or **not relevant**. There are two
separate versions of the study, each at its own link:

| Version | Link | What participants get |
|---|---|---|
| Without AI | `/baseline` | They decide on their own |
| With AI | `/ai` | On request, a **live** AI suggestion (label + confidence) and a "Why?" explanation |

**Live study:** <https://khs14.pythonanywhere.com/>
([without AI](https://khs14.pythonanywhere.com/baseline),
[with AI](https://khs14.pythonanywhere.com/ai))

- **Study design, measures and analysis plan:** [STUDY_DESIGN.md](STUDY_DESIGN.md)
- **Deploying to PythonAnywhere:** [DEPLOY.md](DEPLOY.md)

---

## Synchronous AI inference

The AI version does **not** show precomputed answers. The trained model from
Assignment 2 (TF-IDF word + character n-grams → logistic regression, decision
threshold 0.80) runs **inside the web server, in real time**, every time a
participant asks for it.

```
participant clicks "Show AI suggestion"  (or presses A)
        │  POST /api/ai/suggest  {position, reveal_ms}
        ▼
Flask server ── clf.predict([headline], threshold=0.80)   ← live inference, ~1.5 ms
        │        score → label (Relevant / Not relevant)
        │        score → confidence band (High / Medium / Low)
        │        log the call  → ai_calls (call_type = "suggest")
        │        log the reveal → events (ai_suggestion_shown, reveal_ms)
        ▼
browser shows ONLY the label + band   (no raw score, no ground truth)

participant clicks "Why?"
        │  POST /api/ai/explain
        ▼
leave-one-word-out occlusion: re-score the headline once per word removed
(one batched live call, ~2.5 ms) → top 3 words toward each label,
highlighted in place → logged to ai_calls (call_type = "explain")
```

- **Loaded once, called live.** The model is loaded when the server starts
  (`ai_service.py`) and called on demand. On a single PythonAnywhere worker a
  prediction takes about 1.5 ms and an explanation about 2.5 ms.
- **Logged in full.** The assignment asks to log the outputs shown and the
  follow-up interactions. Every model call is a row in `ai_calls`: headline,
  raw score, label, band, model name, threshold, inference time, cache hit and
  fallback flag. Every answer in `responses` records whether the suggestion was
  shown, how many ms after the headline appeared it was requested, what was
  shown, whether "Why?" was opened (how often, for how long), and whether the
  final answer agreed with the AI.
- **Hidden reference prediction.** The model also runs (hidden, logged) when
  each headline loads, in both versions. This gives agreement-with-AI even
  when the suggestion was not viewed, and a Without-AI reference point for
  reliance.
- **Verified against Assignment 2.** `scripts/check_model.py` re-scores all 298
  study headlines live and compares them with the saved scores: maximum
  difference 0.000000, 0 label mismatches (locally and on PythonAnywhere).
- **Never breaks the session.** If a live call ever failed, the precomputed
  score would be used and the call flagged `fallback = 1` (expected count: 0,
  shown on `/admin`).

---

## HCI design principles applied

**Human-AI interaction**
- AI help on request: the suggestion appears only when the participant asks.
- Confidence shown (High / Medium / Low) with a bar meter, matching the AI's real accuracy.
- "Why?" explanation highlights the words behind each suggestion.
- The AI only suggests; the participant always decides.
- Per-headline feedback helps people learn when to trust the AI.
- The AI's limits are stated up front ("the AI can be wrong").

**Usability**
- Progress bar, version badge and "saved" confirmations show where you are.
- Key definitions one click away on every headline; tutorial with worked examples.
- Next is disabled until an answer and a confidence are chosen.
- Stop and resume any time; review or delete your submission at the end.
- Same layout in both versions; they differ only in the AI box.
- One task per screen, calm design, short text.

**Accessibility**
- Colour-blind-safe colours, and no meaning carried by colour alone (▲ ▼ ✓ ✗, bars, labels).
- Keyboard shortcuts (R / N, 1–5, A, Enter) and visible focus.
- Responsive from small phones to desktops, with large touch targets.

**Ethics**
- Explicit, voluntary consent, with an option to decline.
- No personal details; participants can stay anonymous.
- Each version takes well under 10 minutes.
- Simple attention checks, announced in advance, with one retry.

---

## What the app records

Answers, correctness (computed on the server), confidence (1–5), time per
headline, tab switches, answer changes, AI use (shown or not, when, "Why?"
opened and for how long), a short NASA-TLX workload survey, AI trust and
usefulness ratings, and optional comments. The admin page (`/admin`, password
protected) exports `analysis.csv` (one row per headline answered) and
`sessions.csv` (one row per session, with `person_key` linking each person's two
versions for paired tests).

**Sampling:** each session draws 12 headlines (6 relevant, 6 not) from the
298-headline study set, plus one attention check. With-AI sessions always
contain exactly 2 headlines the AI gets wrong. Less-used headlines are
preferred, and a person never sees the same headline in both versions.

## Layout

```
app.py              Flask routes (participant API, admin, exports)
ai_service.py       live prediction, fallback, "Why?" occlusion explanation
config.py           threshold, confidence bands, study settings, secrets loading
db.py               SQLite schema (init is safe to re-run)
sampling.py         headline sampling for each session
relevance_model.py  model code from Assignment 2 (unchanged; needed to load the model)
model_out/          saved logistic regression + model_card.json
study_dataset_submission.csv   the 298-headline study set (headline, model score, ground truth)
tutorial_examples.json         tutorial headlines (not from the study set)
templates/, static/ plain HTML/CSS/JS frontend (no framework)
scripts/            check_model, prepare_trials, init_db, simulate_participants,
                    backup_db, reset_db
tests/              pytest suite (31 tests, made-up headlines)
```

The database and `instance/secrets.json` are not in the repository, and neither
is any exported participant data.

## Run locally

Requires **Python 3.11** (the model was trained with scikit-learn 1.5.0 on 3.11).

**Windows (PowerShell)**, one command at a time:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
$env:STUDY_SECRET_KEY="dev"; $env:STUDY_ADMIN_PASSWORD="dev"; $env:STUDY_UNIQNAME_KEY="dev"
.venv\Scripts\python scripts\check_model.py
.venv\Scripts\python scripts\prepare_trials.py
.venv\Scripts\python app.py
```

**macOS / Linux:**

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
export STUDY_SECRET_KEY=dev STUDY_ADMIN_PASSWORD=dev STUDY_UNIQNAME_KEY=dev
python scripts/check_model.py
python scripts/prepare_trials.py
python app.py
```

`check_model.py` must end with `RESULT: OK - live model matches the CSV`.
Then open <http://127.0.0.1:5000/baseline> and <http://127.0.0.1:5000/ai>
(admin: <http://127.0.0.1:5000/admin>, password `dev`).

**Tests:** `python -m pytest -q`

**Simulated participants** (with the app running):
`python scripts/simulate_participants.py --n 20 --admin-password dev`.
Clear test data afterwards with `python scripts/reset_db.py --yes`.
