/*
 * app.js: the participant-facing study, in plain JavaScript.
 *
 * The server decides which stage the participant is at (/api/state), so a
 * refresh always resumes in the right place. This file only:
 *   - shows one screen at a time,
 *   - measures timing (headline rendered -> Next clicked) and tab visibility,
 *   - sends answers and events to the server.
 * The browser never receives ground truth before an answer is submitted.
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ helpers
  const COND = document.body.dataset.condition;      // "baseline" or "ai"
  const API = "/api/" + COND;                         // every call goes to this version's API
  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const show = (el, on = true) => { el.hidden = !on; };

  function showScreen(name) {
    $$(".screen").forEach((s) => show(s, s.id === "screen-" + name));
    show($("#loading"), false);
    currentScreen = name;
    window.scrollTo(0, 0);
  }

  /*
   * api(): fetch JSON. On a network error or a 5xx response, show the
   * "couldn't reach the server" banner with a Retry button and try the same
   * request again when it's clicked. 4xx responses go back to the caller.
   */
  function api(path, body) {
    const opts = body === undefined
      ? { method: "GET", credentials: "same-origin" }
      : { method: "POST", credentials: "same-origin",
          headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
    return new Promise((resolve) => {
      const attempt = () => {
        fetch(API + path, opts)
          .then((r) => {
            if (r.status >= 500) throw new Error("server " + r.status);
            return r.json().then((data) => ({ status: r.status, data }));
          })
          .then((res) => { show($("#net-error"), false); resolve(res); })
          .catch(() => {
            show($("#net-error"), true);
            $("#net-retry").onclick = () => { show($("#net-error"), false); attempt(); };
          });
      };
      attempt();
    });
  }

  // Fire-and-forget event logging (never blocks the participant).
  function logEvent(type, payload) {
    fetch(API + "/event", {
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type, payload: payload || null, client_time: new Date().toISOString() }),
    }).catch(() => {});
  }

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ------------------------------------------------------------------ state
  let currentScreen = null;
  let trial = null;          // current trial payload from the server
  let t = null;              // per-trial measurements

  // Put the shared definitions into every slot.
  $$(".definitions-slot").forEach((slot) =>
    slot.appendChild($("#definitions-template").content.cloneNode(true)));

  // ------------------------------------------------------------------ routing
  function route(state) {
    switch (state.stage) {
      case "consent": return showScreen("consent");
      case "tutorial": logEvent("instructions_opened"); return showScreen("tutorial");
      case "trials": return startOrTrial(state);
      case "questionnaire": return showQuestionnaire();
      case "completed": return showCompleted(state);
      case "terminated": return showTerminated();
      case "withdrawn":
        return showMessage("Your submission was deleted", "All your answers have been removed. Thank you for your time.");
      default: return showMessage("Something went wrong", "Please refresh the page.");
    }
  }

  function loadState() {
    api("/state").then(({ data }) => route(data));
  }

  function showMessage(title, text) {
    $("#message-title").textContent = title;
    $("#message-text").textContent = text;
    showScreen("message");
  }

  // ------------------------------------------------------------------ consent
  $("#anonymous").addEventListener("change", (e) => show($("#anon-note-off"), !e.target.checked));
  $("#consent-check").addEventListener("change", (e) => { $("#consent-submit").disabled = !e.target.checked; });
  $("#consent-decline").addEventListener("click", () =>
    showMessage("No problem", "You chose not to take part. Nothing was recorded. You can close this page."));

  $("#consent-form").addEventListener("submit", (e) => {
    e.preventDefault();
    if (!$("#consent-check").checked) {
      $("#consent-error").textContent = "Please tick the consent box to take part.";
      return show($("#consent-error"), true);
    }
    const uniqname = $("#uniqname").value.trim().toLowerCase();
    if (!/^[a-z]{3,8}$/.test(uniqname)) {
      $("#consent-error").textContent = "Please enter your uniqname (3–8 letters, the part of your UMich email before @).";
      return show($("#consent-error"), true);
    }
    api("/start", { consent: true, uniqname, anonymous: $("#anonymous").checked })
      .then(({ status, data }) => {
        if (status >= 400) {
          $("#consent-error").textContent = data.message || "Could not start.";
          return show($("#consent-error"), true);
        }
        show($("#consent-error"), false);
        route(data);
      });
  });

  // ------------------------------------------------------------------ tutorial + instruction check
  $("#tutorial-done").addEventListener("click", () => {
    $$("input[name=check]").forEach((r) => { r.checked = false; });
    $("#check-submit").disabled = true;
    show($("#check-form"), true);
    show($("#check-feedback"), false);
    showScreen("check");
  });
  $$("input[name=check]").forEach((r) =>
    r.addEventListener("change", () => { $("#check-submit").disabled = false; }));

  $("#check-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const chosen = $("input[name=check]:checked");
    if (!chosen) return;
    $("#check-submit").disabled = true;
    api("/attention", { answer: chosen.value }).then(({ status, data }) => {
      if (status >= 400) return loadState();
      if (data.terminated) return loadState();
      show($("#check-form"), false);
      const box = $("#check-feedback");
      box.classList.toggle("fb-good", data.correct);
      box.classList.toggle("fb-bad", !data.correct);
      $("#check-result").innerHTML = data.correct
        ? "<strong>✓ Correct.</strong> “Political climate” is about politics, not the environment."
        : "<strong>✗ Not quite.</strong> Please re-read the rule below and try again. <strong>You have one more try</strong>; a second miss ends the study.";
      $("#check-rule").textContent = data.correct ? "" : data.rule;
      $("#check-continue").textContent = data.retry ? "Try again" : "Continue";
      show(box, true);
      $("#check-continue").onclick = () => {
        if (!data.retry) return loadState();
        $$("input[name=check]").forEach((r) => { r.checked = false; });
        $("#check-submit").disabled = true;
        show(box, false);
        show($("#check-form"), true);
      };
    });
  });

  // ------------------------------------------------------------------ intro (before the first headline)
  function startOrTrial(state) {
    if (state.answered === 0) {
      const ai = COND === "ai";
      $("#intro-part").textContent = ai ? "Version: With AI" : "Version: Without AI";
      $("#intro-title").textContent = ai ? "You can ask an AI for help" : "You decide on your own";
      $("#intro-text").textContent = ai
        ? "On each headline, click “Show AI suggestion” (or press A) to see the AI's answer and how confident it is, then “Why?” to see which words it relied on. It's optional, and the AI can be wrong: the decision is yours."
        : "No AI suggestions in this version. Just read each headline and decide.";
      logEvent("intro_shown");
      $("#intro-start").disabled = false;
      $("#intro-start").onclick = () => { $("#intro-start").disabled = true; loadNextTrial(); };
      showScreen("intro");
    } else {
      loadNextTrial();
    }
  }

  // ------------------------------------------------------------------ trials
  function loadNextTrial() {
    api("/trial/next").then(({ status, data }) => {
      if (status >= 400 || data.stage !== "trials") return status >= 400 ? loadState() : route(data);
      renderTrial(data);
    });
  }

  function renderTrial(data) {
    trial = data;
    t = { answer: null, confidence: null, changes: 0, tabHidden: false,
          whyOpenedAt: null, whyOpenMs: 0, whyCount: 0, whyLoaded: false, start: null, submitting: false,
          aiShown: false, aiRevealMs: null, aiLoading: false };

    // progress
    $("#trial-count").textContent = data.is_attention_check ? "Quick check" : `Headline ${data.index} of ${data.total}`;
    $("#trial-part").textContent = COND === "ai" ? "With AI" : "Without AI";
    const done = data.index - 1;
    $("#progress-fill").style.width = (100 * done / data.total) + "%";
    $("#progress-bar").setAttribute("aria-valuenow", String(done));

    // headline, split into words so the "Why?" panel can highlight them in place
    const words = data.title.split(/\s+/).filter(Boolean);
    $("#headline").innerHTML = words.map((w, i) => `<span class="w" data-i="${i}">${esc(w)}</span>`).join(" ");

    // AI box (AI version only; never on the attention check)
    // The AI's answer is NOT sent with the headline: it is fetched live only when
    // the participant clicks "Show AI suggestion".
    const hasAI = COND === "ai" && !data.is_attention_check;
    show($("#ai-box"), hasAI);
    if (hasAI) {
      show($("#ai-reveal"), true);
      $("#ai-reveal").disabled = false;
      show($("#ai-loading"), false);
      show($("#ai-content"), false);
      show($("#ai-optional"), true);
      $("#why-btn").setAttribute("aria-expanded", "false");
      $("#why-btn").textContent = "Why?";
      show($("#why-panel"), false);
      show($("#why-content"), false);
    }
    $("#key-defs").open = false;

    // reset controls
    $$(".answer-btn, .conf-btn").forEach((b) => { b.classList.remove("selected"); b.setAttribute("aria-pressed", "false"); b.disabled = false; });
    $("#next-btn").disabled = true;
    show($("#next-btn"), true);
    show($("#trial-feedback"), false);
    showScreen("trial");

    // timing starts when the headline has actually been painted
    requestAnimationFrame(() => requestAnimationFrame(() => { t.start = performance.now(); }));
  }

  function select(kind, value) {
    if (!trial || t.submitting || currentScreen !== "trial" || !$("#trial-feedback").hidden) return;
    if (kind === "answer") {
      if (t.answer !== null && t.answer !== value) t.changes += 1;
      t.answer = value;
      $$(".answer-btn").forEach((b) => {
        const on = Number(b.dataset.answer) === value;
        b.classList.toggle("selected", on); b.setAttribute("aria-pressed", String(on));
      });
    } else {
      t.confidence = value;
      $$(".conf-btn").forEach((b) => {
        const on = Number(b.dataset.conf) === value;
        b.classList.toggle("selected", on); b.setAttribute("aria-pressed", String(on));
      });
    }
    $("#next-btn").disabled = !(t.answer !== null && t.confidence !== null);
  }

  $$(".answer-btn").forEach((b) => b.addEventListener("click", () => select("answer", Number(b.dataset.answer))));
  $$(".conf-btn").forEach((b) => b.addEventListener("click", () => select("confidence", Number(b.dataset.conf))));

  // ---- "Show AI suggestion": live model call on request
  function revealAI() {
    if (!trial || COND !== "ai" || trial.is_attention_check || t.aiShown || t.aiLoading
        || t.submitting || !$("#trial-feedback").hidden) return;
    t.aiLoading = true;
    const revealMs = Math.round(performance.now() - (t.start || performance.now()));
    $("#ai-reveal").disabled = true;
    show($("#ai-loading"), true);
    const forTrial = trial;
    api("/suggest", { position: trial.position, reveal_ms: revealMs }).then(({ status, data }) => {
      if (trial !== forTrial) return;
      t.aiLoading = false;
      show($("#ai-loading"), false);
      if (status >= 400) { $("#ai-reveal").disabled = false; return; }
      t.aiShown = true; t.aiRevealMs = revealMs;
      const levels = { High: 3, Medium: 2, Low: 1 };
      const box = $("#ai-result");
      box.dataset.label = data.label;   // colour: blue = Relevant, orange = Not relevant
      box.dataset.band = data.band;     // intensity: High strongest, Low lightest
      $("#ai-label").textContent = (data.label === "relevant" ? "▲ " : "▼ ") + data.label_text;
      $("#ai-band").textContent = data.band;
      $$("#ai-meter i").forEach((bar, i) => bar.classList.toggle("on", i < levels[data.band]));
      $("#ai-score").textContent = data.score_pct !== undefined ? ` · ${data.score_pct}%` : "";
      show($("#ai-reveal"), false);
      show($("#ai-optional"), false);
      show($("#ai-content"), true);
    });
  }
  $("#ai-reveal").addEventListener("click", revealAI);

  // ---- "Why?" explanation
  function closeWhy() {
    if (t && t.whyOpenedAt !== null) {
      t.whyOpenMs += performance.now() - t.whyOpenedAt;
      t.whyOpenedAt = null;
      logEvent("explanation_closed", { position: trial.position, headline_id: trial.headline_id });
    }
    $$("#headline .w").forEach((w) => w.classList.remove("hl-rel", "hl-not"));
    $$("#headline .marker").forEach((m) => m.remove());
    show($("#why-panel"), false);
    $("#why-btn").setAttribute("aria-expanded", "false");
    $("#why-btn").textContent = "Why?";
  }

  function highlight(items, cls, symbol) {
    items.forEach((it) => {
      const w = $(`#headline .w[data-i="${it.index}"]`);
      if (!w) return;
      w.classList.add(cls);
      const m = document.createElement("span");
      m.className = "marker"; m.textContent = symbol; m.setAttribute("aria-hidden", "true");
      w.appendChild(m);
    });
  }

  function applyWhy(data) {
    show($("#why-loading"), false);
    if (!data.available) {
      $("#why-loading").textContent = "Explanation not available for this headline.";
      return show($("#why-loading"), true);
    }
    const list = (items) => items.length ? items.map((i) => `“${esc(i.word)}”`).join(", ") : "<span class='muted'>none</span>";
    $("#why-rel").innerHTML = list(data.toward_relevant);
    $("#why-not").innerHTML = list(data.toward_not_relevant);
    highlight(data.toward_relevant, "hl-rel", "▲");
    highlight(data.toward_not_relevant, "hl-not", "▼");
    show($("#why-content"), true);
  }

  $("#why-btn").addEventListener("click", () => {
    if (!trial || !t.aiShown || !$("#trial-feedback").hidden) return;
    if (!$("#why-panel").hidden) return closeWhy();
    show($("#why-panel"), true);
    $("#why-btn").setAttribute("aria-expanded", "true");
    $("#why-btn").textContent = "Hide why";
    t.whyOpenedAt = performance.now();
    t.whyCount += 1;
    logEvent("explanation_opened", { position: trial.position, headline_id: trial.headline_id });
    if (t.whyLoaded) return applyWhy(t.whyData);
    $("#why-loading").textContent = "Working it out…";
    show($("#why-loading"), true);
    const forTrial = trial;
    api("/explain", { position: trial.position }).then(({ status, data }) => {
      if (trial !== forTrial || $("#why-panel").hidden) return;
      if (status >= 400) data = { available: false };
      t.whyLoaded = true; t.whyData = data;
      applyWhy(data);
    });
  });

  // ---- submit
  function submitTrial() {
    if (!trial || $("#next-btn").disabled || t.submitting) return;
    t.submitting = true;
    const clientMs = Math.round(performance.now() - (t.start || performance.now()));
    if (t.whyOpenedAt !== null) { t.whyOpenMs += performance.now() - t.whyOpenedAt; t.whyOpenedAt = null; }
    $("#next-btn").disabled = true;
    $$(".answer-btn, .conf-btn").forEach((b) => { b.disabled = true; });

    api("/trial/answer", {
      position: trial.position, answer: t.answer, confidence: t.confidence,
      client_time_ms: clientMs, answer_changed_count: t.changes, tab_hidden_during_trial: t.tabHidden,
      explanation_opened: t.whyCount > 0, explanation_open_count: t.whyCount,
      explanation_open_ms: Math.round(t.whyOpenMs),
      ai_revealed: t.aiShown, ai_reveal_ms: t.aiRevealMs,
    }).then(({ status, data }) => {
      if (status >= 400) return loadState();   // duplicate / out of order: resync with the server
      showFeedback(data);
    });
  }
  $("#next-btn").addEventListener("click", submitTrial);

  function showFeedback(data) {
    show($("#next-btn"), false);
    const box = $("#trial-feedback");
    box.classList.toggle("fb-good", data.your_answer_correct);
    box.classList.toggle("fb-bad", !data.your_answer_correct);
    if (data.is_attention_check) {
      $("#fb-main").innerHTML = data.your_answer_correct
        ? "<strong>✓ Thanks!</strong> Attention check passed."
        : "<strong>✗ Attention check missed.</strong> Please read it carefully. <strong>You have one more try</strong>; a second miss ends the study.";
    } else {
      $("#fb-main").innerHTML = (data.your_answer_correct ? "<strong>✓ Correct.</strong> " : "<strong>✗ Not quite.</strong> ")
        + `The answer is <strong>${esc(data.correct_label_text)}</strong>.`;
    }
    $("#fb-ai").textContent = data.ai_was_correct === undefined ? ""
      : (data.ai_was_correct ? "The AI's suggestion was correct." : "The AI's suggestion was wrong on this one.");
    show(box, true);
    $("#fb-continue").focus({ preventScroll: true });
    box.scrollIntoView({ behavior: "smooth", block: "nearest" });
    $("#fb-continue").onclick = () => {
      $("#fb-continue").onclick = null;
      closeWhy(); trial = null;
      if (data.terminated) return loadState();
      api("/trial/next").then(({ status, data: next }) => {
        if (status >= 400) return loadState();
        if (next.stage === "trials") renderTrial(next); else route(next);
      });
    };
  }

  // ---- tab visibility during a trial
  document.addEventListener("visibilitychange", () => {
    const hidden = document.visibilityState === "hidden";
    if (hidden && trial && currentScreen === "trial") t.tabHidden = true;
    logEvent(hidden ? "tab_hidden" : "tab_visible",
      trial ? { position: trial.position, screen: currentScreen } : { screen: currentScreen });
  });


  // ---- keyboard shortcuts: R / N, 1-5, Enter
  document.addEventListener("keydown", (e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea") return;
    if (currentScreen !== "trial") return;
    const k = e.key.toLowerCase();
    if (!$("#trial-feedback").hidden) {
      if (k === "enter") { e.preventDefault(); $("#fb-continue").click(); }
      return;
    }
    if (k === "a") revealAI();
    else if (k === "r") select("answer", 1);
    else if (k === "n") select("answer", 0);
    else if (["1", "2", "3", "4", "5"].includes(k)) select("confidence", Number(k));
    else if (k === "enter") { e.preventDefault(); submitTrial(); }
  });

  // ------------------------------------------------------------------ questionnaire
  const TLX = [
    ["tlx_mental", "How mentally demanding was this task?", "Very low", "Very high"],
    ["tlx_effort", "How hard did you have to work to do well?", "Very low", "Very high"],
    ["tlx_frustration", "How frustrated, stressed or annoyed did you feel?", "Very low", "Very high"],
    ["tlx_performance", "How successful do you think you were?", "Very unsuccessful", "Very successful"],
  ];
  const AI_ITEMS = [
    ["ai_trust", "I trusted the AI's suggestions.", "Strongly disagree", "Strongly agree"],
    ["ai_useful", "The AI's suggestions were useful.", "Strongly disagree", "Strongly agree"],
    ["ai_why_helped", "The “Why?” explanations helped me decide.", "Strongly disagree", "Strongly agree", true],
  ];

  function scaleHtml([name, text, lo, hi, allowNA]) {
    const opts = [1, 2, 3, 4, 5, 6, 7].map((v) =>
      `<label class="scale-opt"><input type="radio" name="${name}" value="${v}"><span>${v}</span></label>`).join("");
    const na = allowNA ? `<label class="scale-na small"><input type="radio" name="${name}" value="na"> I didn't use them</label>` : "";
    return `<fieldset class="scale"><legend>${esc(text)}</legend><div class="scale-row">${opts}</div>
      <div class="conf-ends small muted"><span>${esc(lo)}</span><span>${esc(hi)}</span></div>${na}</fieldset>`;
  }

  const qItems = () => TLX.concat(COND === "ai" ? AI_ITEMS : []);

  function showQuestionnaire() {
    $("#q-part").textContent = COND === "ai" ? "Version: With AI" : "Version: Without AI";
    $("#q-items").innerHTML = qItems().map(scaleHtml).join("");
    show($("#q-comment-wrap"), COND === "ai");
    $("#q-comment").value = "";
    show($("#q-error"), false);
    showScreen("questionnaire");
  }

  $("#q-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const payload = {};
    for (const [name] of qItems()) {
      const c = $(`input[name=${name}]:checked`);
      if (!c) {
        $("#q-error").textContent = "Please answer every question.";
        return show($("#q-error"), true);
      }
      payload[name] = c.value === "na" ? null : Number(c.value);
    }
    if (COND === "ai") payload.comment = $("#q-comment").value;
    api("/questionnaire", payload).then(({ status, data }) => {
      if (status >= 400) { $("#q-error").textContent = data.message || "Please try again."; return show($("#q-error"), true); }
      route(data);
    });
  });

  // ------------------------------------------------------------------ completed: results, review, delete
  function showCompleted(state) {
    $("#completion-code").textContent = state.completion_code || "";
    showScreen("completed");
    selectTab("results");
  }

  function pct(x) { return x === null || x === undefined ? "–" : Math.round(x * 100) + "%"; }

  function selectTab(which) {
    $("#tab-results").setAttribute("aria-selected", String(which === "results"));
    $("#tab-review").setAttribute("aria-selected", String(which === "review"));
    show($("#panel-results"), which === "results");
    show($("#panel-review"), which === "review");
    if (which === "results") loadResults(); else loadReview();
  }
  $("#tab-results").addEventListener("click", () => selectTab("results"));
  $("#tab-review").addEventListener("click", () => selectTab("review"));

  function loadResults() {
    logEvent("results_opened");
    api("/results").then(({ status, data }) => {
      if (status >= 400) return;
      const cell = (d) => `<td>${pct(d.accuracy)}</td><td>${d.mean_seconds ?? "–"} s</td><td>${d.people}</td>`;
      $("#panel-results").innerHTML = `
        <h2>Results board</h2>
        <p class="small muted">Everyone who has finished so far. Names are never shown.</p>
        <div class="table-wrap"><table>
          <thead><tr><th></th><th>Accuracy</th><th>Time / headline</th><th>People</th></tr></thead>
          <tbody>
            <tr class="row-you"><th scope="row">You (${COND === "ai" ? "with AI" : "without AI"})</th>${cell(data.you)}</tr>
            <tr><th scope="row">Everyone, without AI</th>${cell(data.baseline)}</tr>
            <tr><th scope="row">Everyone, with AI</th>${cell(data.ai)}</tr>
          </tbody>
        </table></div>
        ${data.ai_alone_accuracy !== null ? `<p>The AI on its own was right on <strong>${pct(data.ai_alone_accuracy)}</strong> of the headlines shown in the AI version.</p>` : ""}
        <p>Your accuracy is equal to or better than <strong>${pct(data.share_you_beat_or_tied)}</strong> of people in this version.</p>`;
    });
  }

  function loadReview() {
    logEvent("review_opened");
    api("/review").then(({ status, data }) => {
      if (status >= 400) return;
      const real = data.responses.filter((r) => !r.is_attention_check);
      const right = real.filter((r) => r.correct).length;
      let n = 0;
      const cards = data.responses.map((r) => {
        const num = r.is_attention_check ? "Check" : `#${++n}`;
        const ai = COND === "ai" && !r.is_attention_check
          ? `<div><dt>AI said</dt><dd>${esc(r.ai_suggestion || "–")}</dd></div>` : "";
        return `<li class="review-item ${r.correct ? "is-right" : "is-wrong"}">
          <div class="review-top"><span class="review-num">${num}</span>
            <span class="review-mark">${r.correct ? "✓ Correct" : "✗ Wrong"}</span></div>
          <p class="review-title">${esc(r.title)}</p>
          <dl class="review-meta">
            <div><dt>You said</dt><dd>${esc(r.your_answer)}</dd></div>
            <div><dt>Answer</dt><dd>${esc(r.correct_label)}</dd></div>${ai}
            <div><dt>Sure</dt><dd>${r.confidence}/5</dd></div>
            <div><dt>Time</dt><dd>${r.seconds ?? "–"} s</dd></div>
          </dl></li>`;
      }).join("");
      $("#panel-review").innerHTML = `<h2>Your answers</h2>
        <p>You got <strong>${right} of ${real.length}</strong> headlines right.</p>
        <ol class="review-list">${cards}</ol>`;
    });
  }

  $("#feedback-form").addEventListener("submit", (e) => {
    e.preventDefault();
    api("/feedback", { feedback: $("#final-feedback").value }).then(({ status }) => {
      if (status < 400) show($("#feedback-sent"), true);
    });
  });

  $("#withdraw-btn").addEventListener("click", () => { show($("#withdraw-confirm"), true); show($("#withdraw-btn"), false); });
  $("#withdraw-no").addEventListener("click", () => { show($("#withdraw-confirm"), false); show($("#withdraw-btn"), true); });
  $("#withdraw-yes").addEventListener("click", () => {
    api("/withdraw", { confirm: true }).then(({ data }) => route(data));
  });

  // ------------------------------------------------------------------ terminated
  function showTerminated() {
    showScreen("terminated");
  }

  // ------------------------------------------------------------------ go
  loadState();
})();
