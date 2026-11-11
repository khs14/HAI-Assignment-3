"""
ai_service.py
=============
The synchronous AI. The model is loaded ONCE when the app starts; every trial
request calls `predict()` live, and the "Why?" button calls `explain()`.

If live prediction fails for any reason, `predict()` falls back to the
precomputed score from the trials table and marks the result fallback=True, so
the participant never sees a crash.
"""
from __future__ import annotations

import re
import time

import config

_HAS_WORD = re.compile(r"[A-Za-z0-9]")


class AIService:
    def __init__(self, model_dir=config.MODEL_DIR, model_name=config.MODEL_NAME,
                 threshold=config.THRESHOLD):
        self.model_name = model_name
        self.threshold = float(threshold)
        self.clf = None
        self.load_error = None
        self._explain_cache: dict[str, dict] = {}
        try:
            from relevance_model import RelevanceClassifier
            self.clf = RelevanceClassifier.load(str(model_dir), model=model_name)
            self.clf.predict(["warm-up"], threshold=self.threshold)  # first call is slower
        except Exception as exc:  # the app still runs, using precomputed scores
            self.load_error = f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------ #
    def predict(self, title: str, fallback_score: float) -> dict:
        """Live prediction for one headline. Never raises."""
        t0 = time.perf_counter()
        error = None
        try:
            if self.clf is None:
                raise RuntimeError(f"model not loaded ({self.load_error})")
            score = float(self.clf.predict([title], threshold=self.threshold)[0]["score"])
            fallback = False
        except Exception as exc:
            score = float(fallback_score)
            fallback = True
            error = f"{type(exc).__name__}: {exc}"[:500]
        return {
            "score": score,
            "label": config.label_for(score, self.threshold),
            "band": config.band_for(score, self.threshold),
            "inference_ms": round((time.perf_counter() - t0) * 1000, 3),
            "fallback": fallback,
            "error": error,
        }

    # ------------------------------------------------------------------ #
    def explain(self, headline_id: str, title: str) -> dict:
        """
        Leave-one-word-out occlusion. For each whitespace-separated word i:
            delta_i = score(full headline) - score(headline without word i)
        delta_i > 0  -> the word pushes toward Relevant
        delta_i < 0  -> the word pushes toward Not relevant
        Returns the top-K words in each direction, by word index (so the browser
        can highlight them in place). Results are cached per headline.
        Never raises: on failure returns empty lists and an error message.
        """
        if headline_id in self._explain_cache:
            return {**self._explain_cache[headline_id], "cached": True, "inference_ms": 0.0}

        t0 = time.perf_counter()
        words = title.split()
        try:
            if self.clf is None:
                raise RuntimeError(f"model not loaded ({self.load_error})")
            idx = [i for i, w in enumerate(words) if _HAS_WORD.search(w)]
            variants = [title] + [" ".join(words[:i] + words[i + 1:]) for i in idx]
            scores = self.clf.predict_proba(variants)  # one batched call
            base = float(scores[0])
            deltas = [(i, base - float(s)) for i, s in zip(idx, scores[1:])]
            k, eps = config.EXPLAIN_TOP_K, config.EXPLAIN_MIN_DELTA
            pos = sorted((d for d in deltas if d[1] > eps), key=lambda d: -d[1])[:k]
            neg = sorted((d for d in deltas if d[1] < -eps), key=lambda d: d[1])[:k]
            result = {
                "toward_relevant": [{"index": i, "word": words[i], "delta": round(d, 4)} for i, d in pos],
                "toward_not_relevant": [{"index": i, "word": words[i], "delta": round(d, 4)} for i, d in neg],
                "n_predictions": len(variants),
                "error": None,
            }
            self._explain_cache[headline_id] = result
        except Exception as exc:
            result = {"toward_relevant": [], "toward_not_relevant": [], "n_predictions": 0,
                      "error": f"{type(exc).__name__}: {exc}"[:500]}
        return {**result, "cached": False,
                "inference_ms": round((time.perf_counter() - t0) * 1000, 3)}
