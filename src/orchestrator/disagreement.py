"""Pairwise disagreement detection.

Given a set of Reviews, flag the reviewer pairs whose positions diverge enough
that a rebuttal round is warranted.

Two-signal combination (both normalized to 0-1, higher = more disagreement):
  1. score_spread_norm   = |score_a - score_b| / 9
  2. summary_cosine_dist = 1 - cos_sim(embed(summary_a), embed(summary_b))

Why two signals: score alone misses reviewers who give similar scores for
opposite reasons ("accept for novelty" vs "accept despite methodology holes").
Semantic similarity alone misses reviewers writing similar prose while reaching
opposite verdicts.

combined = 0.5 * score_spread_norm + 0.5 * summary_cosine_dist

Flagged if combined >= Config.DISAGREEMENT_THRESHOLD (default 0.35).
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
from sentence_transformers import SentenceTransformer

from src.config import Config
from src.schemas import Disagreement, Review

# Eager-load the embedding model at import time (same rationale as rag/stats_verifier:
# lazy loads inside tight code paths cost seconds). Model is already cached locally
# after Phase 3's first use.
_model = SentenceTransformer("all-MiniLM-L6-v2")


def _cosine_distance(a: str, b: str) -> float:
    a_s = (a or "").strip()
    b_s = (b or "").strip()
    if not a_s or not b_s:
        return 1.0
    emb = _model.encode([a_s, b_s], convert_to_numpy=True, show_progress_bar=False)
    dot = float(np.dot(emb[0], emb[1]))
    norm = float(np.linalg.norm(emb[0]) * np.linalg.norm(emb[1]))
    if norm == 0:
        return 1.0
    return max(0.0, min(1.0, 1.0 - dot / norm))


def compute_pairwise_disagreements(reviews: dict[str, Review]) -> list[Disagreement]:
    """Return a Disagreement record for every reviewer pair (C(n,2) entries)."""
    ids = sorted(reviews.keys())
    out: list[Disagreement] = []
    threshold = Config.DISAGREEMENT_THRESHOLD
    for a, b in combinations(ids, 2):
        ra, rb = reviews[a], reviews[b]
        spread = abs(ra.overall_score - rb.overall_score)
        score_norm = spread / 9.0
        dist = _cosine_distance(ra.summary, rb.summary)
        combined = 0.5 * score_norm + 0.5 * dist
        out.append(
            Disagreement(
                reviewer_a=a,
                reviewer_b=b,
                score_spread=round(spread, 3),
                summary_cosine_distance=round(dist, 3),
                combined_score=round(combined, 3),
                flagged=combined >= threshold,
            )
        )
    return out


def flagged_only(disagreements: list[Disagreement]) -> list[Disagreement]:
    return [d for d in disagreements if d.flagged]
