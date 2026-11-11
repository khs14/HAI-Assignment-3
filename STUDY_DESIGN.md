# Study design: AI assistance for sustainability-headline relevance

CSE 594, Assignment 3. This summary is meant to be reused in the write-up.

## Research question

Does a live AI suggestion (label + confidence band, with an optional word-level
"Why?" explanation) change how accurately, quickly and confidently people judge
whether a news headline is relevant to environmental sustainability? And do
people rely on the AI *appropriately*, following it when it is right more than
when it is wrong?

## Task

Participants read English news headlines (US and India editions of Google News)
and label each one **Relevant** (main subject is the environment, climate or
sustainability) or **Not relevant**. A narrow weather rule applies: a storm,
flood or fire is relevant only when linked to an environmental cause, impact,
pollution or policy. Words used in another sense ("political climate",
"Miami Heat") do not count.

## AI system

- TF-IDF (word + character n-grams) logistic regression, trained from scratch
  (no pretrained weights), decision threshold **0.80**.
- On the 298-headline study set: accuracy 0.859, precision 0.962, recall 0.729,
  **42 errors (38 false negatives, 4 false positives)**.
- **Synchronous, on request:** in the With-AI version the suggestion is hidden
  until the participant clicks **Show AI suggestion** (or presses A). At that
  moment the server runs the model live (median under 1 ms) and returns only
  the label and confidence band. Every call is logged (`ai_calls`: headline,
  raw score, label, band, model, threshold, inference time, fallback flag),
  together with when the participant asked (`ai_reveal_ms`, ms after the
  headline appeared) and whether they then opened "Why?". The model also runs,
  hidden and logged, on every headline in both versions, so agreement with the
  AI can be computed even when the suggestion was not viewed. If live inference
  fails, the precomputed score is used and the call is flagged (`fallback`).
- **Why on request:** a suggestion people must ask for separates *seeking*
  advice from *following* it, and shows how often people want help at all
  (a reliance measure in itself).
- **Shown to participants:** label and a confidence band derived from the score.
  The band never contradicts the label. It is shown with a 3-bar meter and
  colour intensity (High = 3 bars, strongest tint, solid border; Medium = 2
  bars, lighter tint; Low = 1 bar, faint tint, dashed border), plus the word,
  so it doesn't rely on colour alone:

  | Label | Band | Score | Headlines in study set | AI accuracy in band |
  |---|---|---|---|---|
  | Relevant | High | ≥ 0.95 | 65 | 100% |
  | Relevant | Medium | 0.80–0.95 | 41 | 90% |
  | Not relevant | Low | 0.50–0.80 | 28 | 29% |
  | Not relevant | Medium | 0.20–0.50 | 29 | 52% |
  | Not relevant | High | < 0.20 | 135 | 97% |

  The raw score can also be shown as a percentage (`SHOW_SCORE` in
  `config.py`); it is off for this study.
- **"Why?" explanation:** leave-one-word-out occlusion. Each word is removed in
  turn, the headline is re-scored, and the change in score is that word's
  importance. The top 3 words pushing toward Relevant and the top 3 pushing
  toward Not relevant are highlighted in the headline (blue ▲ / orange ▼, plus
  text labels). This method is model-agnostic and faithful to the model's real
  behaviour, which means it sometimes highlights words a person would find
  irrelevant (e.g. "Why", "Is"), because the model uses character fragments.

## Design

- **Two separate interfaces**, as the assignment requires, each a complete
  session at its own link:
  - `/baseline`: **Without AI**
  - `/ai`: **With AI** (live suggestion + "Why?")
- **Within-subjects via the class rotation.** Each classmate does the task in
  one version, then later in the other. The course's rotation (3 tasks from the
  Without-AI sheet and 3 from the With-AI sheet, then the same tasks in the other
  condition) means about half the participants see each version first. The app
  records which version each person did first (`version_order`).
- **Linking a person's two sessions.** Participants enter their uniqname. It is
  stored only as a keyed hash (plus in plain text if they don't tick "Keep me
  anonymous"). The hash links the same person's Without-AI and With-AI sessions
  (`person_key` in the exports), which allows paired comparisons, and means a
  person never sees the same headline in both versions.
- **12 headlines per session** plus one in-task attention check, about
  5 minutes including instructions (the course limit is under 10 minutes).
- **Per-trial feedback in both versions:** after each answer is locked in,
  participants see the correct label, and in the AI version whether the AI was
  right. Feedback lets people calibrate their trust in the AI during the
  session. It also means people learn as they go, which can carry over into
  their second version (see the analysis section).
- **No Back button**, so timing and reliance measures reflect a single decision.
- **Key definitions** are one click away in a dropdown above each headline
  (closed by default, to keep the screen calm).
- **Explicit, voluntary consent:** the welcome page says the study is
  voluntary and unpaid, with no penalty for not taking part, and that people
  can stop at any time. Continue only works after ticking a consent box, and
  there is an "I don't want to take part" option that records nothing.
- **One session per uniqname per version.** No restart after termination.

### Flow (each version)

Consent → tutorial (definitions, weather rule, hard negatives, 6 worked
examples; the AI version also explains the AI box) → instruction check → intro
→ 12 headlines (+ attention check) → questionnaire → completion code, results
board, review of own answers, option to delete the submission.

## Sampling (fixed at consent; a refresh resumes)

- **Without-AI session:** 6 relevant + 6 not relevant (by ground truth).
- **With-AI session:** exactly **2 headlines the AI gets wrong** + 10 it gets
  right, balanced to 6/6 by ground truth. The 2 errors are drawn from the full
  error pool, so they keep the AI's natural false-negative-heavy mix (38 FN : 4 FP).
  The session's AI error rate (2/12 = 16.7%) is close to the AI's true error rate
  on the study set (42/298 = 14.1%).
- No headline appears twice in a session, and none of a person's With-AI
  headlines repeat their Without-AI headlines (or vice versa).
- **Even coverage:** headlines are drawn with weight 1 / (1 + times already
  assigned to a session that was not terminated or withdrawn), so the 298
  headlines are used about equally.
- Headlines are shuffled. The attention check is placed at a random position
  between 4 and 9.

## Data quality

- **Instruction check** after the tutorial: "A headline about the 'political
  climate' in Congress is…" (correct: Not relevant).
- **In-task attention check** at a random position 4–9: "This is an attention
  check. Please select Not relevant."
- **Rule (stated on the welcome page and in the tutorial):** a first miss of
  either check shows a warning and the **same check again**. A second miss
  (on either check) **ends the session**, and that uniqname **cannot take that
  version again**. The other version stays available, because the class
  rotation needs everyone to do both. This follows the course guidance:
  simple checks, with some tolerance (termination only after repeated misses).
- What is recorded: `attention_failures` (0, 1 or 2) on each session, every
  answer to the instruction check in order (`instruction_check_answer`, e.g.
  `relevant,not_relevant`), and an `attention_check_failed` event for each miss.
  Terminated sessions stay in the data, marked `terminated`; exclude them from
  the analysis. Report how many sessions needed a retry.
- **Tab visibility** is recorded per trial (`tab_hidden_during_trial`) so trials
  with the tab hidden can be excluded from timing analyses.
- **Withdrawal:** participants can delete their submission after finishing; all
  their trial, questionnaire, event and AI-call rows are removed.

## Measures and why each one

| Measure | Definition | Why |
|---|---|---|
| **Accuracy** | share of trials where answer = ground truth, per participant × condition | The primary outcome: does the AI help people decide correctly? |
| **False negative rate** | share of truly relevant headlines labelled Not relevant | The AI's main failure mode is missing relevant headlines (38 of 42 errors). This shows whether people inherit that bias with AI help. |
| **False positive rate** | share of truly not-relevant headlines labelled Relevant | The other error type. Hard negatives ("political climate") test whether people fall for keywords. |
| **AI viewing rate** | share of With-AI headlines where the participant clicked "Show AI suggestion", and how long after the headline appeared (`ai_reveal_ms`) | How much people *seek* AI help, separately from whether they follow it. Viewing late suggests checking their own answer; viewing early suggests delegating. |
| **Reliance when AI is right** | agreement with the AI's label on With-AI trials where the AI is correct (only trials where the suggestion was viewed) | Appropriate reliance: people should follow a correct AI. |
| **Reliance when AI is wrong** | agreement with the AI's label on trials where the AI is wrong | Over-reliance: following a wrong AI. The gap between this and the row above is the key appropriate-reliance measure. |
| **Baseline agreement with the hidden AI** | same agreement, computed in the Without-AI version, where the AI ran but was not shown | Reference point: how often people agree with the AI anyway. The difference from the AI condition is the *effect of seeing* the suggestion. |
| **Time per trial** | ms from the headline being rendered to Next, measured in the browser | Whether AI help speeds up or slows down decisions (reading the suggestion and explanation costs time). |
| **Per-trial confidence** | 1 (not at all sure) to 5 (very sure) | Whether AI help makes people more confident, and whether confidence tracks correctness (calibration). |
| **Explanation use** | whether "Why?" was opened, how many times, how long | How often people seek explanations, and whether opening one relates to correctly rejecting a wrong AI. |
| **Workload (NASA-TLX, 4 items)** | mental demand, effort, frustration, perceived performance (1–7) at the end of each session | Whether AI assistance changes perceived workload. Perceived performance can be compared with real accuracy. |
| **Trust and usefulness** (AI version only) | "I trusted the AI's suggestions", "The AI's suggestions were useful", "The 'Why?' explanations helped me decide" (1–7, strongly disagree → strongly agree; the last has a "didn't use them" option) | Subjective trust, compared with behavioural reliance. People can say they distrust the AI and still follow it. |
| **Answer changes** | times the selected answer was switched before Next | A rough signal of hesitation or conflict with the AI. |

NASA-TLX note: the performance item is scored 1 = very unsuccessful to
7 = very successful (higher = better), the reverse of the original TLX
"perfect → failure" wording. Reverse it if you combine it with the other items
into a workload score.

## Suggested analysis

Exports from `/admin`:
- `analysis.csv`: one row per real trial.
- `sessions.csv`: one row per session with accuracy, FN/FP rates, mean time,
  mean confidence, reliance, and questionnaire answers. This is the easiest
  starting point.

1. **Exclusions:** use `session_status == completed`. Report how many sessions
   were terminated or withdrawn. For timing, also drop trials with
   `tab_hidden_during_trial == 1` and very long times (e.g. > 60 s).
2. **Main comparison (paired):** for people who completed both versions
   (same `person_key`), compare Without-AI vs With-AI accuracy, FN rate, FP rate,
   mean time, mean confidence and each TLX item with a **Wilcoxon signed-rank
   test** (or a paired t-test if the differences look roughly normal). Report
   means with 95% CIs. If some people completed only one version, also report
   an unpaired comparison over all completed sessions (Mann-Whitney U) as a
   check.
3. **Order effects:** compare the With-AI minus Without-AI difference for people
   who did Without-AI first vs With-AI first (`version_order`). With more data,
   a mixed-effects logistic regression:
   `correct ~ condition * version_order + (1 | person_key) + (1 | headline_id)`.
4. **Reliance:** in the AI version, agreement rate when `ai_correct == 1` vs
   `ai_correct == 0`, on trials where the suggestion was viewed
   (`ai_revealed == 1`). Also report the viewing rate, and compare accuracy on
   viewed vs. not-viewed trials (descriptive; people choose when to look). Each session has only 2 AI errors, so pool these trials
   across participants (e.g. 10 people → 20 AI-wrong trials) and report the
   pooled rate with a bootstrap CI over participants.
5. **Explanation:** compare accuracy on AI-wrong trials with vs. without
   "Why?" opened (descriptive; this is not randomised).
6. **Questionnaire:** correlate trust ratings with each person's behavioural
   reliance (`agree_ai_when_right`, `agree_ai_when_wrong` in `sessions.csv`).

## Limitations to mention

- Small sample of classmates (convenience sample, familiar with AI).
- Only 2 AI errors per session, so over-reliance estimates are noisy per
  person and are best reported pooled.
- Per-trial feedback creates learning effects that can carry over into a
  person's second version; the class rotation balances order and
  `version_order` lets you check it, but the estimates are of AI help *with*
  feedback.
- Version order is set by the class rotation, not by the app, so it is only
  approximately balanced; check the counts in `sessions.csv`.
- The AI's error mix is dominated by false negatives, so conclusions about
  over-reliance mostly concern missed relevant headlines.
- Occlusion explanations reflect the model faithfully, including words that
  look unrelated to the topic; this may lower trust in the explanations.
- Headline-only task; English; US and India editions only.
